"""Phase2 DEBUG adapter tests (DEBUG-owned scope only).

Covers, per the Phase2 contract:
- DAP Content-Length framing: round trip, partial chunks, batching, hostile
  headers, oversized messages (real parser, no fake readiness);
- request/response correlation and async event dispatch, including
  out-of-order responses, failed responses, transport death and oversized
  inbound frames, exercised against an in-process scripted DAP peer;
- input validation: canonical UUID sessions, relative .py program paths,
  breakpoint bounds, snapshot bounds, redaction of sensitive variable names;
- real end-to-end lifecycle against an actual ``python3 -m debugpy.adapter``
  subprocess when debugpy is available (temp venv) — initialize, launch with
  stopOnEntry, setBreakpoints, configurationDone, stopped-on-entry, inspect
  (stack/scopes/variables, redaction), continue, next, terminated, owned stop
  with validated snapshot export and NO evaluate request ever sent;
- negative paths and owned cleanup semantics: unknown/invalid sessions,
  invalid state transitions, duplicate sessions, reaper auto-stop on session
  deadline / idle / unexpected target death, changed_state_unknown when no
  snapshot can be trusted;
- Docker argv stays fixed and hardened; Docker end-to-end tests run only when
  a docker daemon and the phase2 image are actually present (honest skip
  locally; root runs the no-skip Linux VPS smoke).
"""
import asyncio
import base64
import importlib.util
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

SOURCE = pathlib.Path(__file__).resolve().parent.parent
_CR_DIR = SOURCE / 'backend' / 'coding_runtime'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


da = _load('debug_adapter_under_test', _CR_DIR / 'debug_adapter.py')
vol_io = _load('volume_io_for_debug_tests', _CR_DIR / 'volume_io.py')


def _debugpy_python():
    candidates = []
    env = os.environ.get('HARMONIKA_DEBUGPY_PYTHON')
    if env:
        candidates.append(env)
    candidates.append(sys.executable)
    for exe in candidates:
        try:
            proc = subprocess.run([exe, '-c', 'import debugpy'],
                                  capture_output=True, timeout=60)
            if proc.returncode == 0:
                return exe
        except (OSError, subprocess.SubprocessError):
            continue
    return None


DEBUGPY_PYTHON = _debugpy_python()


def _docker_binary():
    exe = shutil.which('docker') or ('/usr/bin/docker' if os.path.exists('/usr/bin/docker') else None)
    if exe is None:
        return None
    try:
        proc = subprocess.run([exe, 'info'], capture_output=True, timeout=30)
        if proc.returncode == 0:
            return exe
    except (OSError, subprocess.SubprocessError):
        return None
    return None


DOCKER = _docker_binary()


def _docker_image_present(exe, image):
    try:
        proc = subprocess.run([exe, 'image', 'inspect', image], capture_output=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


TARGET_SOURCE = '''import sys

SECRET_TOKEN = 'hunter2-do-not-leak'


def compute(n):
    total = 0
    for i in range(n):
        total += i
    return total


if __name__ == '__main__':
    payload = {'user': 'tester', 'password': 'p@ss'}
    result = compute(10)
    print('result', result)
'''

TARGET_LINES = TARGET_SOURCE.split('\n')
BREAK_LINE = TARGET_LINES.index('        total += i') + 1

SLOW_TARGET_SOURCE = '''import time

time.sleep(30)
print('done')
'''


def b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


class LocalDebugAdapter(da.DockerDebugAdapter):
    """Same lifecycle logic, real local debugpy adapter, plain temp directory."""

    def __init__(self, python_exe, root, **kwargs):
        super().__init__(**kwargs)
        self._py = python_exe
        self._root = pathlib.Path(root)
        self.calls = {'prepare': 0, 'freeze': 0, 'thaw': 0, 'export': 0, 'teardown': 0}

    def _session_dir(self, session):
        return self._root / ('ws-' + session.id)

    def _workspace_prefix(self, session):
        return str(self._session_dir(session))

    def _program_path(self, session, rel):
        return str(self._session_dir(session) / rel)

    def _main_cwd(self, session):
        return session.local_dir

    def _main_argv(self, session):
        return [self._py, '-m', 'debugpy.adapter']

    async def _prepare_workspace(self, session, payload):
        self.calls['prepare'] += 1
        ws = self._session_dir(session)
        ws.mkdir(parents=True, exist_ok=True)
        session.local_dir = str(ws)
        vol_io.do_import(str(ws), json.loads(payload.decode('utf-8')))

    async def _workspace_alive(self, session):
        return bool(session.local_dir) and os.path.isdir(session.local_dir)

    async def _freeze(self, session):
        self.calls['freeze'] += 1
        session.frozen = True
        return True

    async def _thaw(self, session):
        self.calls['thaw'] += 1
        session.frozen = False

    async def _export_snapshot(self, session):
        self.calls['export'] += 1
        result = vol_io.do_export(session.local_dir)
        snapshot = result['snapshot']
        da.validate_snapshot_mapping(snapshot)
        return {key: snapshot[key] for key in sorted(snapshot)}, list(result.get('warnings') or [])

    async def _teardown(self, session):
        self.calls['teardown'] += 1
        await self._thaw(session)
        if session.local_dir and os.path.isdir(session.local_dir):
            shutil.rmtree(session.local_dir, ignore_errors=True)


class FramingTests(unittest.TestCase):
    def test_roundtrip_single_message(self):
        message = {'seq': 1, 'type': 'request', 'command': 'initialize',
                   'arguments': {'adapterID': 'harmonika-debug'}}
        parser = da.DapFrameParser()
        messages = parser.feed(da.encode_dap_message(message))
        self.assertEqual(messages, [message])

    def test_partial_chunks_and_split_utf8_body(self):
        message = {'type': 'event', 'event': 'output', 'body': {'output': 'héllo ✓'}}
        frame = da.encode_dap_message(message)
        parser = da.DapFrameParser()
        collected = []
        for index in range(len(frame)):
            collected.extend(parser.feed(frame[index:index + 1]))
        self.assertEqual(collected, [message])

    def test_two_messages_in_one_chunk(self):
        first = {'seq': 1, 'type': 'request', 'command': 'threads'}
        second = {'seq': 2, 'type': 'request', 'command': 'scopes'}
        parser = da.DapFrameParser()
        messages = parser.feed(da.encode_dap_message(first) + da.encode_dap_message(second))
        self.assertEqual(messages, [first, second])

    def test_partial_body_waits_for_more(self):
        frame = da.encode_dap_message({'seq': 1, 'type': 'request', 'command': 'threads'})
        parser = da.DapFrameParser()
        self.assertEqual(parser.feed(frame[:len(frame) - 1]), [])
        self.assertEqual(len(parser.feed(frame[-1:])), 1)

    def test_extra_headers_tolerated(self):
        body = json.dumps({'ok': True}).encode('utf-8')
        frame = (b'Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n'
                 b'Content-Length: %d\r\n\r\n' % len(body)) + body
        self.assertEqual(da.DapFrameParser().feed(frame), [{'ok': True}])

    def test_missing_content_length_rejected(self):
        with self.assertRaises(da.DapProtocolError):
            da.DapFrameParser().feed(b'X-Bogus: 1\r\n\r\n{}')

    def test_garbage_header_line_rejected(self):
        with self.assertRaises(da.DapProtocolError):
            da.DapFrameParser().feed(b'not-a-header\r\n\r\n{}')

    def test_nonnumeric_length_rejected(self):
        with self.assertRaises(da.DapProtocolError):
            da.DapFrameParser().feed(b'Content-Length: many\r\n\r\n{}')

    def test_oversized_declared_length_rejected(self):
        with self.assertRaises(da.DapProtocolError):
            da.DapFrameParser().feed(b'Content-Length: 99999999\r\n\r\n')

    def test_header_only_overflow_rejected(self):
        parser = da.DapFrameParser()
        with self.assertRaises(da.DapProtocolError):
            for _ in range(200):
                parser.feed(b'A' * 64)

    def test_encode_rejects_oversize_outbound(self):
        with self.assertRaises(da.DapProtocolError):
            da.encode_dap_message({'blob': 'x' * (da.DAP_MESSAGE_MAX + 1)})


class ValidationTests(unittest.TestCase):
    def test_session_uuid_accepts_only_canonical(self):
        uid = str(uuid.uuid4())
        self.assertEqual(da.validate_session_uuid(uid), uid)
        for bad in (None, 5, 'not-a-uuid', uid.upper(), uuid.uuid4().hex,
                    'urn:uuid:' + uid):
            with self.assertRaises(da.DebugAdapterError):
                da.validate_session_uuid(bad)

    def test_session_names_deterministic_and_owned(self):
        uid = str(uuid.uuid4())
        names = da.session_names(uid)
        self.assertEqual(set(names), {'container', 'volume', 'keeper', 'importer', 'exporter'})
        for key, name in names.items():
            self.assertTrue(name.startswith('hms-debug-'), name)
            self.assertIn(uid, name)
        self.assertEqual(names, da.session_names(uid))
        self.assertEqual(names['container'], 'hms-debug-' + uid)
        self.assertEqual(names['volume'], 'hms-debug-vol-' + uid)

    def test_program_path_rules(self):
        snapshot = {'app/main.py': b'x'}
        self.assertEqual(da.validate_program_path('app/main.py', snapshot), 'app/main.py')
        for bad, code in [('/abs.py', 'invalid_path'), ('../escape.py', 'invalid_path'),
                          ('a/../b.py', 'invalid_path'), ('main.txt', 'invalid_path'),
                          ('missing.py', 'path_not_in_snapshot'), ('.', 'invalid_path'),
                          ('a\\b.py', 'invalid_path'), ('a\x00b.py', 'invalid_path')]:
            with self.assertRaises(da.DebugAdapterError) as ctx:
                da.validate_program_path(bad, snapshot)
            self.assertEqual(ctx.exception.code, code, bad)

    def test_breakpoints_rules(self):
        clean = da.validate_breakpoints([{'path': 'a.py', 'lines': [3, 1, 3]}])
        self.assertEqual(clean, [{'path': 'a.py', 'lines': [1, 3]}])
        self.assertEqual(da.validate_breakpoints(None), [])
        for bad in ([{'path': 'a.py', 'lines': []}],
                    [{'path': 'a.py', 'lines': ['x']}],
                    [{'path': 'a.py', 'lines': [True]}],
                    [{'path': 'a.py', 'lines': [0]}],
                    [{'path': 'a.py', 'lines': [da.MAX_LINE_NUMBER + 1]}],
                    [{'path': 'noext', 'lines': [1]}],
                    [{'path': '/a.py', 'lines': [1]}],
                    [{'path': 'a.py'}],
                    ['nope'],
                    [{'path': 'a.py', 'lines': list(range(1, da.MAX_BREAKPOINT_LINES + 2))}]):
            with self.assertRaises(da.DebugAdapterError):
                da.validate_breakpoints(bad)

    def test_snapshot_bounds(self):
        ok = da.validate_snapshot_mapping({'a.py': b64('print(1)')})
        self.assertEqual(ok, {'a.py': b'print(1)'})
        for bad in (None, [], {'a.py': '!!!not-base64!!!'},
                    {('x' * 300) + '.py': b64('y')},
                    {'big.py': b64('y' * (da.SNAPSHOT_MAX_FILE_BYTES + 1))},
                    {('f%04d.py' % i): b64('y') for i in range(da.SNAPSHOT_MAX_FILES + 1)},
                    {'nested/../evil.py': b64('y')}):
            with self.assertRaises(da.DebugAdapterError):
                da.validate_snapshot_mapping(bad)

    def test_redaction(self):
        self.assertTrue(da.redact_name('password'))
        self.assertTrue(da.redact_name('PASSWORD'))
        self.assertTrue(da.redact_name('SECRET_TOKEN'))
        self.assertTrue(da.redact_name('api_key'))
        self.assertTrue(da.redact_name('authHeader'))
        self.assertTrue(da.redact_name('session_key'))
        self.assertFalse(da.redact_name('total'))
        self.assertFalse(da.redact_name('result'))

    def test_bound_helpers(self):
        self.assertEqual(da.bound_text('x' * 500), 'x' * 200 + '…')
        bounded = da.bound_json({'deep': {'a': ['b' * 900]}}, depth=0)
        self.assertEqual(bounded['deep']['a'][0], 'b' * 200 + '…')
        self.assertEqual(da.bound_text(None), '')


class FakeAdapter:
    """Scripted DAP peer over a socketpair (no subprocess)."""

    async def __aenter__(self):
        client_sock, server_sock = socket.socketpair()
        self.client_reader, self.client_writer = await asyncio.open_connection(sock=client_sock)
        self.server_reader, self.server_writer = await asyncio.open_connection(sock=server_sock)
        self._parser = da.DapFrameParser()
        self._pending = []
        return self

    async def __aexit__(self, *exc):
        for writer in (self.server_writer,self.client_writer):
            try:writer.close();await writer.wait_closed()
            except OSError:pass

    async def send(self, message):
        self.server_writer.write(da.encode_dap_message(message))
        await self.server_writer.drain()

    async def recv(self, timeout: float = 5.0):
        """Next request frame; one persistent parser so batched frames survive."""
        deadline = time.monotonic() + timeout
        while not self._pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError('fake peer timed out waiting for a request')
            try:
                chunk = await asyncio.wait_for(self.server_reader.read(4096), remaining)
            except asyncio.TimeoutError:
                raise AssertionError('fake peer timed out waiting for a request')
            if not chunk:
                raise ConnectionError('client closed')
            self._pending.extend(self._parser.feed(chunk))
        return self._pending.pop(0)


class FakePeerDapTests(unittest.IsolatedAsyncioTestCase):
    async def test_correlation_survives_out_of_order_responses(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            first = asyncio.ensure_future(client.request('initialize', {'x': 1}))
            second = asyncio.ensure_future(client.request('threads', {}))
            request_a = await peer.recv()
            request_b = await peer.recv()
            self.assertEqual([request_a['command'], request_b['command']],
                             ['initialize', 'threads'])
            # respond to the SECOND request first
            await peer.send({'seq': 100, 'type': 'response', 'request_seq': request_b['seq'],
                             'success': True, 'command': 'threads', 'body': {'threads': [7]}})
            await peer.send({'seq': 101, 'type': 'response', 'request_seq': request_a['seq'],
                             'success': True, 'command': 'initialize', 'body': {'ok': True}})
            self.assertEqual((await first)['body'], {'ok': True})
            self.assertEqual((await second)['body'], {'threads': [7]})
            await client.close()

    async def test_event_dispatch_callback_and_wait(self):
        seen = []

        async with FakeAdapter() as peer:
            client = da.DapClient('test', on_event=lambda entry: seen.append(entry))
            await client.attach(peer.client_reader, peer.client_writer)
            waiter = asyncio.ensure_future(client.wait_event('stopped', 5))
            await peer.send({'seq': 1, 'type': 'event', 'event': 'stopped',
                             'body': {'threadId': 3, 'reason': 'entry'}})
            entry = await waiter
            self.assertEqual(entry['body']['reason'], 'entry')
            self.assertEqual(seen[0]['event'], 'stopped')
            self.assertEqual(seen[0]['body']['threadId'], 3)
            await client.close()

    async def test_failed_response_raises_specific_error(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            pending = asyncio.ensure_future(client.request('launch', {}))
            request = await peer.recv()
            await peer.send({'seq': 9, 'type': 'response', 'request_seq': request['seq'],
                             'success': False, 'command': 'launch',
                             'message': 'cannot launch'})
            with self.assertRaises(da.DapResponseError) as ctx:
                await pending
            self.assertEqual(ctx.exception.code, 'dap_response_failed')
            self.assertEqual(ctx.exception.command, 'launch')
            await client.close()

    async def test_transport_close_fails_pending_requests(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            pending = asyncio.ensure_future(client.request('threads', {}))
            await peer.recv()
            peer.server_writer.close()
            with self.assertRaises(da.DapTransportClosed):
                await pending
            self.assertFalse(client.alive)
            await client.close()

    async def test_oversized_inbound_frame_kills_client(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            peer.server_writer.write(b'Content-Length: %d\r\n\r\n'
                                     % (da.DAP_MESSAGE_MAX + 10))
            await peer.server_writer.drain()
            with self.assertRaises(da.DapTransportClosed):
                await client.request('threads', {}, timeout=5)
            self.assertEqual(client.close_reason, 'protocol_error')

    async def test_sent_commands_log_bounded_and_records(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            for index in range(5):
                pending = asyncio.ensure_future(client.request('cmd%d' % index, {}))
                request = await peer.recv()
                await peer.send({'seq': index + 10, 'type': 'response',
                                 'request_seq': request['seq'], 'success': True,
                                 'command': request['command'], 'body': {}})
                await pending
            self.assertEqual(client.sent_commands,
                             ['cmd0', 'cmd1', 'cmd2', 'cmd3', 'cmd4'])
            await client.close()

    async def test_request_timeout_raises_and_unblocks(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            started = time.monotonic()
            with self.assertRaises(da.DebugAdapterError) as ctx:
                await client.request('silence', {}, timeout=0.3)
            self.assertEqual(ctx.exception.code, 'dap_timeout')
            self.assertLess(time.monotonic() - started, 5)
            await client.close()

    async def test_async_event_callback_tasks_bounded_and_dropped(self):
        async with FakeAdapter() as peer:
            release = asyncio.Event()

            async def slow_cb(entry):
                await release.wait()

            client = da.DapClient('test', on_event=slow_cb)
            await client.attach(peer.client_reader, peer.client_writer)
            total = da.MAX_CALLBACK_TASKS + 16
            for index in range(total):
                await peer.send({'seq': index + 1, 'type': 'event',
                                 'event': 'output', 'body': {'n': index}})
            for _ in range(100):
                await asyncio.sleep(0.02)
                if client.dropped_callbacks >= total - da.MAX_CALLBACK_TASKS:
                    break
            # hard bound: never more than MAX_CALLBACK_TASKS live tasks,
            # excess dropped and counted, nothing queued unbounded
            self.assertEqual(len(client._callback_tasks), da.MAX_CALLBACK_TASKS)
            self.assertEqual(client.dropped_callbacks,
                             total - da.MAX_CALLBACK_TASKS)
            release.set()
            for _ in range(100):
                await asyncio.sleep(0.02)
                if not client._callback_tasks:
                    break
            self.assertFalse(client._callback_tasks)
            self.assertEqual(client.dropped_callbacks,
                             total - da.MAX_CALLBACK_TASKS)
            await client.close()

    async def test_callback_exception_retrieved_and_task_reaped(self):
        async with FakeAdapter() as peer:
            hits = []

            async def boom(entry):
                hits.append(entry)
                raise RuntimeError('callback boom')

            client = da.DapClient('test', on_event=boom)
            await client.attach(peer.client_reader, peer.client_writer)
            await peer.send({'seq': 1, 'type': 'event', 'event': 'output',
                             'body': {}})
            for _ in range(100):
                await asyncio.sleep(0.02)
                if hits and not client._callback_tasks:
                    break
            self.assertEqual(len(hits), 1)
            # the failing task was reaped and its exception retrieved
            self.assertFalse(client._callback_tasks)
            # the client itself stays healthy and keeps dispatching events
            pending = asyncio.ensure_future(client.wait_event('stopped', 5))
            await peer.send({'seq': 2, 'type': 'event', 'event': 'stopped',
                             'body': {'threadId': 1}})
            entry = await pending
            self.assertEqual(entry['body']['threadId'], 1)
            await client.close()

    async def test_stderr_pump_tracked_and_reaped_on_close(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            feed = asyncio.StreamReader()
            client._pump_stderr(feed)
            self.assertIsNotNone(client._stderr_task)
            feed.feed_data(b'adapter noise')
            for _ in range(100):
                await asyncio.sleep(0.02)
                if 'adapter noise' in client.stderr_text():
                    break
            self.assertIn('adapter noise', client.stderr_text())
            # no EOF: the pump task is still pending; close must cancel and
            # await it (tracked/reaped, never left dangling)
            await client.close()
            self.assertIsNotNone(client._stderr_task)
            self.assertTrue(client._stderr_task.done())
            self.assertFalse(client._callback_tasks)

    async def test_async_close_callback_tracked_and_reaped_on_close(self):
        async with FakeAdapter() as peer:
            entered = asyncio.Event()

            async def closing(reason):
                entered.set()
                await asyncio.Event().wait()

            client = da.DapClient('test')
            client.on_close(closing)
            await client.attach(peer.client_reader, peer.client_writer)
            client._finish('eof')
            for _ in range(100):
                await asyncio.sleep(0.02)
                if entered.is_set():
                    break
            self.assertTrue(entered.is_set())
            self.assertEqual(len(client._callback_tasks), 1)
            # close() must cancel and await the hanging close-callback task
            await client.close()
            self.assertFalse(client._callback_tasks)

    async def test_finish_handles_full_event_queues_without_queuefull(self):
        async with FakeAdapter() as peer:
            client = da.DapClient('test')
            await client.attach(peer.client_reader, peer.client_writer)
            queue = client._event_queues['stopped']
            for index in range(queue.maxsize):
                queue.put_nowait({'kind': 'event', 'event': 'stopped',
                                  'body': {'n': index}})
            self.assertTrue(queue.full())
            client._finish('queue_full_test')  # must not raise QueueFull
            self.assertFalse(client.alive)
            self.assertEqual(client.close_reason, 'queue_full_test')
            seen = []
            with self.assertRaises(da.DapTransportClosed):
                while True:
                    seen.append(await client.wait_event('stopped', 0.5))
            # exactly one buffered entry was sacrificed for the closure
            # sentinel so a waiter always observes the transport closure
            self.assertEqual(len(seen), queue.maxsize - 1)
            await client.close()


class AdapterNegativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_session_ids_rejected_before_any_spawn(self):
        adapter = da.DockerDebugAdapter(binary='false-docker-must-not-run')
        result = await adapter.start({'a.py': b64('x')}, {'path': 'a.py'}, 'not-a-uuid')
        self.assertEqual(result['error'], 'invalid_session_id')

    async def test_invalid_start_args(self):
        uid = str(uuid.uuid4())
        adapter = da.DockerDebugAdapter(binary='false-docker-must-not-run')
        cases = [
            ({'a.py': b64('x')}, 'not-a-dict', uid, 'invalid_request'),
            ({'a.py': b64('x')}, {'path': 'a.py', 'extra': 1}, uid, 'invalid_request'),
            ({'a.py': b64('x')}, {'path': 'sub/../../a.py'}, uid, 'invalid_path'),
            ({'a.py': b64('x')}, {'path': 'a.txt'}, uid, 'invalid_path'),
            ({'a.py': b64('x')}, {'path': 'missing.py'}, uid, 'path_not_in_snapshot'),
            ({'a.py': b64('x')}, {'path': 'a.py', 'timeout_seconds': 'x'}, uid, 'invalid_request'),
            ({'a.py': b64('x')}, {'path': 'a.py', 'timeout_seconds': 9999}, uid, 'invalid_request'),
            ({'a.py': b64('x')}, {'path': 'a.py', 'breakpoints': [{'path': 'a.py', 'lines': [0]}]},
             uid, 'invalid_breakpoints'),
            ({'a.py': '!!!'}, {'path': 'a.py'}, uid, 'invalid_snapshot'),
            (None, {'path': 'a.py'}, uid, 'invalid_snapshot'),
        ]
        for snapshot, args, session, code in cases:
            result = await adapter.start(snapshot, args, session)
            self.assertEqual(result['error'], code, (args, result))
            self.assertEqual(result['status'], 'failed')
        status = adapter.session_state(uid)
        self.assertEqual(status['state'], 'unknown')

    async def test_action_gate(self):
        uid = str(uuid.uuid4())
        adapter = da.DockerDebugAdapter(binary='false-docker-must-not-run')
        for action in ('status', 'continue', 'next', 'inspect'):
            result = await adapter.action(uid, action)
            self.assertEqual(result['error'], 'unknown_session')
        self.assertEqual((await adapter.action(uid, 'evaluate'))['error'], 'invalid_action')
        self.assertEqual((await adapter.action(uid, 'exec'))['error'], 'invalid_action')
        bad = await adapter.action('junk', 'status')
        self.assertEqual(bad['error'], 'invalid_session_id')

    def test_action_wait_bound_fits_root_action_timeout(self):
        # root REST action timeout is 20s: the continue/next observation wait
        # plus any two DAP sub-requests must always fit inside it
        self.assertLessEqual(da.ACTION_WAIT_MAX_SECONDS, 8)
        self.assertLessEqual(
            da.ACTION_WAIT_MAX_SECONDS + 2 * da.LOCATION_TIMEOUT_SECONDS, 20)

    async def test_set_breakpoints_gate(self):
        uid = str(uuid.uuid4())
        adapter = da.DockerDebugAdapter(binary='false-docker-must-not-run')
        self.assertEqual((await adapter.set_breakpoints(uid, 'a.py', [1]))['error'],
                         'unknown_session')
        self.assertEqual((await adapter.set_breakpoints(uid, 'a.txt', [1]))['error'],
                         'invalid_path')
        self.assertEqual((await adapter.set_breakpoints(uid, 'a.py', 'x'))['error'],
                         'invalid_breakpoints')
        # empty lines is a VALID explicit "clear breakpoints" request
        self.assertEqual((await adapter.set_breakpoints(uid, 'a.py', []))['error'],
                         'unknown_session')

    async def test_stop_unknown_and_cleanup_names_validation(self):
        uid = str(uuid.uuid4())
        adapter = da.DockerDebugAdapter(binary='false-docker-must-not-run')
        result = await adapter.stop(uid)
        self.assertEqual(result['error'], 'unknown_session')
        cleaned = await adapter.cleanup_names(uid)
        self.assertTrue(cleaned['ok'])
        self.assertIn('removed', cleaned)
        with self.assertRaises(da.DebugAdapterError):
            await adapter.cleanup_names('oops')
        active_guard = await adapter.start({'a.py': b64('x')}, {'path': 'a.py', 'timeout_seconds': 5}, uid)
        # docker binary missing -> start fails and aborts; session must not linger
        self.assertEqual(active_guard['status'], 'failed')
        self.assertEqual(adapter.session_state(uid)['state'], 'unknown')

    async def test_production_main_argv_is_fixed_and_hardened(self):
        adapter = da.DockerDebugAdapter(binary='/usr/bin/docker',
                                        image='harmonika-coding:phase2')
        uid = str(uuid.uuid4())
        session = da._Session(uid)
        session.deadline = time.monotonic() + 120
        argv = adapter._main_argv(session)
        name = da.session_names(uid)
        expected = [
            '/usr/bin/docker', 'run', '--rm',
            '--name', name['container'],
            '--interactive',
            '-v', name['volume'] + ':/workspace:nocopy',
            '-w', '/workspace',
        ] + list(da.HARDEN_FLAGS) + list(da.LOG_DRIVER_FLAGS) + list(da.ULIMIT_FLAGS) + [
            '--label', 'harmonika.debug=%s' % uid,
            'harmonika-coding:phase2',
            'timeout', '-k', '2',
        ]
        cap = argv[len(expected)]
        self.assertTrue(cap.isdigit() and 1 <= int(cap) <= 120, cap)
        expected += [cap, 'python3', '-m', 'debugpy.adapter']
        # exact argv equality proves the command is fixed: no user-controlled
        # fragment (program path, args, cwd) can ever reach the docker CLI
        self.assertEqual(argv, expected)

    async def test_launch_arguments_contract(self):
        adapter = da.DockerDebugAdapter()
        uid = str(uuid.uuid4())
        session = da._Session(uid)
        session.path = 'pkg/main.py'
        arguments = adapter._launch_arguments(session, 'pkg/main.py')
        self.assertEqual(arguments, {
            'program': '/workspace/pkg/main.py',
            'cwd': '/workspace',
            'console': 'internalConsole',
            'stopOnEntry': True,
            'justMyCode': True,
        })


async def _drain_pending(tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@unittest.skipUnless(DEBUGPY_PYTHON, 'debugpy not importable; install temp venv and set HARMONIKA_DEBUGPY_PYTHON')
class RealDebugpyLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Real DAP lifecycle: actual debugpy adapter subprocess, own temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='hms-debugpy-test-')
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _adapter(self, **kwargs):
        return LocalDebugAdapter(DEBUGPY_PYTHON, self.root,
                                 session_max_seconds=120, idle_seconds=60, **kwargs)

    def _snapshot(self):
        return {'target.py': b64(TARGET_SOURCE)}

    async def test_full_lifecycle_entry_breakpoint_step_terminate_stop(self):
        adapter = self._adapter()
        events = []
        uid = str(uuid.uuid4())
        result = await adapter.start(
            self._snapshot(),
            {'path': 'target.py',
             'breakpoints': [{'path': 'target.py', 'lines': [BREAK_LINE]}]},
            uid,
            on_event=lambda entry: events.append(entry))
        try:
            self.assertEqual(result['status'], 'ok', result)
            self.assertEqual(result['state'], 'paused', result)
            self.assertEqual(result['stop_reason'], 'entry')
            session = adapter._sessions[uid]
            client = session.client

            inspect = await adapter.action(uid, 'inspect')
            self.assertTrue(inspect['ok'], inspect)
            self.assertTrue(inspect['frames'])
            self.assertTrue(inspect['no_evaluate'])

            status = await adapter.action(uid, 'status')
            self.assertTrue(status['ok'])
            self.assertTrue(status['paused'])

            set_result = await adapter.set_breakpoints(uid, 'target.py', [BREAK_LINE])
            self.assertTrue(set_result['ok'], set_result)
            self.assertTrue(all(bp['verified'] for bp in set_result['breakpoints']),
                            set_result)

            resumed = await adapter.action(uid, 'continue')
            self.assertEqual(resumed['state'], 'paused', resumed)
            self.assertEqual(resumed['stopped_reason'], 'breakpoint', resumed)
            self.assertEqual(resumed['location']['line'], BREAK_LINE, resumed)

            # now SECRET_TOKEN (module global) is defined; inspect must redact
            inspect = await adapter.action(uid, 'inspect')
            self.assertTrue(inspect['ok'], inspect)
            flat_values = [var for scopes in inspect['variables'].values()
                           for scope in scopes.values() for var in scope.values()]
            self.assertTrue(any(var['redacted'] for var in flat_values), flat_values)
            secret_values = [var['value'] for scopes in inspect['variables'].values()
                             for scope in scopes.values()
                             for name, var in scope.items()
                             if 'SECRET' in name.upper() or 'TOKEN' in name.upper()]
            self.assertTrue(secret_values)
            self.assertTrue(all(v == da._REDACTED for v in secret_values))

            stepped = await adapter.action(uid, 'next')
            self.assertEqual(stepped['state'], 'paused', stepped)
            self.assertEqual(stepped['stopped_reason'], 'step', stepped)
            # stepping out of `total += i` lands on the loop-back `for` check
            # or the following statement; both are valid DAP step results
            self.assertIn(stepped['location']['line'],
                          (BREAK_LINE - 1, BREAK_LINE, BREAK_LINE + 1), stepped)

            # clear breakpoints (empty lines = clear) so the final continue
            # is not re-caught by the in-loop breakpoint
            cleared = await adapter.set_breakpoints(uid, 'target.py', [])
            self.assertTrue(cleared['ok'], cleared)
            self.assertEqual(cleared['breakpoints'], [], cleared)

            finished = await adapter.action(uid, 'continue')
            self.assertEqual(finished['state'], 'terminated', finished)

            status = await adapter.action(uid, 'status')
            self.assertTrue(status['terminated'])
            self.assertFalse(status['paused'])

            event_names = {entry['event'] for entry in events}
            self.assertIn('stopped', event_names)
            self.assertIn('terminated', event_names)
            for entry in events:
                self.assertEqual(entry['session_id'], uid)

            self.assertNotIn('evaluate', client.sent_commands)
            self.assertNotIn('evaluateInStackFrame', client.sent_commands)

            stop = await adapter.stop(uid)
            self.assertTrue(stop['ok'], stop)
            self.assertEqual(stop['state'], 'stopped')
            self.assertFalse(stop['changed_state_unknown'], stop)
            self.assertTrue(stop['snapshot'])
            self.assertIn('target.py', stop['snapshot'])
            decoded = base64.b64decode(stop['snapshot']['target.py']).decode('utf-8')
            self.assertEqual(decoded, TARGET_SOURCE)
            self.assertEqual(adapter.calls['export'], 1)
            self.assertGreaterEqual(adapter.calls['freeze'], 0)
        finally:
            await adapter.stop(uid)

    async def test_program_with_syntax_error_never_pauses_and_stop_still_owned(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        broken = {'broken.py': b64('def oops(:\n    pass\n')}
        result = await adapter.start(broken, {'path': 'broken.py'}, uid)
        self.assertIn(result['state'], ('terminated', 'failed', 'paused'), result)
        if result['state'] == 'terminated':
            self.assertTrue(result['status'] == 'ok')
        stop = await adapter.stop(uid)
        self.assertTrue(stop['ok'], stop)
        self.assertEqual(stop['state'], 'stopped')
        self.assertEqual(adapter.session_state(uid)['state'], 'unknown')

    async def test_duplicate_active_session_rejected(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        first = await adapter.start(self._snapshot(), {'path': 'target.py'}, uid)
        try:
            self.assertEqual(first['status'], 'ok', first)
            second = await adapter.start(self._snapshot(), {'path': 'target.py'}, uid)
            self.assertEqual(second['error'], 'duplicate_session')
            other = str(uuid.uuid4())
            third = await adapter.start(self._snapshot(), {'path': 'target.py'}, other)
            self.assertEqual(third['error'], 'session_limit')
        finally:
            await adapter.stop(uid)
        self.assertEqual(adapter.session_state(uid)['state'], 'unknown')

    async def test_unexpected_target_death_marks_unknown_and_suppresses_snapshot(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        result = await adapter.start(self._snapshot(), {'path': 'target.py'}, uid)
        self.assertEqual(result['status'], 'ok', result)
        session = adapter._sessions[uid]
        session.client.proc.kill()
        await session.client.proc.wait()
        await adapter._reap_cycle()
        for _ in range(100):
            if adapter.session_state(uid)['state'] == 'unknown':
                break
            await asyncio.sleep(0.1)
        self.assertEqual(adapter.session_state(uid)['state'], 'unknown')
        cached = await adapter.stop(uid)
        self.assertTrue(cached['ok'])
        self.assertTrue(cached.get('changed_state_unknown'), cached)
        self.assertIsNone(cached.get('snapshot'), cached)
        self.assertEqual(cached.get('status'), 'failed', cached)

    async def test_reaper_stops_expired_session_with_snapshot(self):
        adapter = LocalDebugAdapter(DEBUGPY_PYTHON, self.root,
                                    session_max_seconds=120, idle_seconds=60)
        uid = str(uuid.uuid4())
        result = await adapter.start(self._snapshot(),
                                     {'path': 'target.py', 'timeout_seconds': 6}, uid)
        self.assertEqual(result['status'], 'ok', result)
        session = adapter._sessions[uid]
        session.deadline = time.monotonic() - 0.1  # force expiry
        await adapter._reap_cycle()
        stop = None
        for _ in range(100):
            state = adapter.session_state(uid)
            if state['state'] == 'unknown':
                break
            await asyncio.sleep(0.1)
        cached = await adapter.stop(uid)
        self.assertTrue(cached['ok'])
        self.assertEqual(cached['status'], 'timeout', cached)
        self.assertEqual(cached['reason'], 'session_timeout', cached)
        self.assertTrue(cached['snapshot'], cached)
        self.assertIn('target.py', cached['snapshot'])

    async def test_reaper_stops_idle_session(self):
        adapter = LocalDebugAdapter(DEBUGPY_PYTHON, self.root,
                                    session_max_seconds=120, idle_seconds=5)
        uid = str(uuid.uuid4())
        result = await adapter.start(self._snapshot(), {'path': 'target.py'}, uid)
        self.assertEqual(result['status'], 'ok', result)
        session = adapter._sessions[uid]
        session.last_activity = time.monotonic() - 30
        await adapter._reap_cycle()
        for _ in range(100):
            if adapter.session_state(uid)['state'] == 'unknown':
                break
            await asyncio.sleep(0.1)
        cached = await adapter.stop(uid)
        self.assertEqual(cached['status'], 'idle_timeout', cached)
        self.assertEqual(cached['reason'], 'idle_timeout', cached)

    async def test_stop_is_idempotent_and_returns_cached_result(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        result = await adapter.start(self._snapshot(), {'path': 'target.py'}, uid)
        self.assertEqual(result['status'], 'ok', result)
        first = await adapter.stop(uid)
        second = await adapter.stop(uid)
        self.assertEqual(first, second)
        third = await adapter.action(uid, 'status')
        self.assertEqual(third['error'], 'unknown_session')

    async def test_missing_import_still_yields_entry_pause_and_valid_export(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        snapshot = {'ok.py': b64('x = 1\n'), 'target.py': b64('import missing_module_xyz\n')}
        result = await adapter.start(snapshot, {'path': 'target.py'}, uid)
        # stopOnEntry pauses before the import executes, so the session is
        # deterministic: paused at entry, then owned stop exports cleanly.
        self.assertEqual(result['status'], 'ok', result)
        resumed = await adapter.action(uid, 'continue')
        self.assertIn(resumed['state'], ('paused', 'terminated', 'failed'), resumed)
        stop = await adapter.stop(uid)
        self.assertTrue(stop['ok'])
        self.assertTrue(stop['snapshot'])
        self.assertIn('target.py', stop['snapshot'])
        self.assertIn('ok.py', stop['snapshot'])

    async def test_start_reports_terminated_when_target_finishes_during_configure(self):
        adapter = self._adapter()
        # documented test seam: drop stopOnEntry so the trivial target runs to
        # completion DURING configure and configure observes `terminated`
        def _launch(session, rel):
            return {
                'program': adapter._program_path(session, rel),
                'cwd': adapter._workspace_prefix(session),
                'console': 'internalConsole',
                'stopOnEntry': False,
                'justMyCode': True,
            }
        adapter._launch_arguments = _launch  # type: ignore[method-assign]
        uid = str(uuid.uuid4())
        result = await adapter.start(
            {'done.py': b64("print('done')\n")}, {'path': 'done.py'}, uid)
        # configure observed the target terminate: start must NOT override
        # that observation with a fabricated 'paused' state
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['state'], 'terminated', result)
        self.assertNotEqual(result['stop_reason'], 'entry', result)
        self.assertEqual(adapter.session_state(uid)['state'], 'terminated')
        stop = await adapter.stop(uid)
        self.assertTrue(stop['ok'], stop)
        self.assertFalse(stop['changed_state_unknown'], stop)
        self.assertIsNotNone(stop['snapshot'], stop)
        self.assertIn('done.py', stop['snapshot'])

    async def test_resume_wait_bounded_and_reports_running_truthfully(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        result = await adapter.start({'slow.py': b64(SLOW_TARGET_SOURCE)},
                                     {'path': 'slow.py'}, uid)
        self.assertEqual(result['status'], 'ok', result)
        started = time.monotonic()
        resumed = await adapter.action(uid, 'continue')
        elapsed = time.monotonic() - started
        # no stop possible within the window: the observed truth is 'running',
        # reported honestly (never fabricated as paused/terminated), and the
        # wait is bounded well under root's 20s action timeout
        self.assertTrue(resumed['ok'], resumed)
        self.assertEqual(resumed['state'], 'running', resumed)
        self.assertTrue(resumed['waited'], resumed)
        self.assertTrue(resumed['wait_timeout'], resumed)
        self.assertFalse(resumed.get('terminated'), resumed)
        self.assertLess(elapsed, da.ACTION_WAIT_MAX_SECONDS + 6, elapsed)
        self.assertEqual(adapter.session_state(uid)['state'], 'running')
        # requested stop of a LIVE running target must still freeze + export
        # a valid workspace snapshot
        stop = await adapter.stop(uid)
        self.assertTrue(stop['ok'], stop)
        self.assertFalse(stop['changed_state_unknown'], stop)
        self.assertIsNotNone(stop['snapshot'], stop)
        self.assertIn('slow.py', stop['snapshot'])

    async def test_stop_exports_snapshot_when_disconnect_closes_transport(self):
        adapter = self._adapter()
        uid = str(uuid.uuid4())
        result = await adapter.start(self._snapshot(), {'path': 'target.py'}, uid)
        self.assertEqual(result['status'], 'ok', result)
        session = adapter._sessions[uid]
        client = session.client
        original = client.request

        async def lethal_disconnect(command, arguments=None,
                                    timeout=da.DAP_REQUEST_TIMEOUT_SECONDS):
            if command == 'disconnect':
                # adapter tears the transport down as a result of the requested
                # disconnect WITHOUT ever emitting `terminated`; an older bug
                # misread this as "unexpected death" and suppressed the export
                client.alive = False
                if client.proc is not None and client.proc.returncode is None:
                    client.proc.kill()
                    try:
                        await client.proc.wait()
                    except Exception:
                        pass
                raise da.DapTransportClosed('adapter closed on disconnect')
            return await original(command, arguments, timeout=timeout)

        client.request = lethal_disconnect  # type: ignore[method-assign]
        stop = await adapter.stop(uid)
        self.assertTrue(stop['ok'], stop)
        self.assertFalse(stop['changed_state_unknown'], stop)
        self.assertIsNotNone(stop['snapshot'], stop)
        self.assertIn('target.py', stop['snapshot'])
        decoded = base64.b64decode(stop['snapshot']['target.py']).decode('utf-8')
        self.assertEqual(decoded, TARGET_SOURCE)


class DockerAdapterTests(unittest.IsolatedAsyncioTestCase):
    """Honest-skip Docker tests; root runs the no-skip VPS smoke."""

    @classmethod
    def setUpClass(cls):
        cls.docker = DOCKER

    @unittest.skipUnless(DOCKER, 'docker daemon not available on this host (honest skip; root VPS smoke required)')
    async def test_capabilities_honest(self):
        adapter = da.DockerDebugAdapter(binary=self.docker)
        caps = await adapter.capabilities()
        self.assertIn('docker_available', caps)
        self.assertIn('image_available', caps)
        self.assertIn('debugpy_in_image', caps)
        self.assertIn('ready', caps)
        self.assertIsInstance(caps['error'] or '', str)

    @unittest.skipUnless(DOCKER, 'docker daemon not available on this host (honest skip; root VPS smoke required)')
    async def test_full_docker_lifecycle(self):
        image = da.IMAGE_DEFAULT
        if not _docker_image_present(self.docker, image):
            self.skipTest('image %s not built on this host (honest skip; root builds image)' % image)
        adapter = da.DockerDebugAdapter(binary=self.docker, image=image,
                                        session_max_seconds=120, idle_seconds=60)
        uid = str(uuid.uuid4())
        result = await adapter.start({'target.py': b64(TARGET_SOURCE)},
                                     {'path': 'target.py', 'timeout_seconds': 90}, uid)
        try:
            self.assertEqual(result['status'], 'ok', result)
            resumed = await adapter.action(uid, 'continue')
            self.assertIn(resumed['state'], ('paused', 'terminated'))
            stop = await adapter.stop(uid)
            self.assertTrue(stop['ok'])
        finally:
            await adapter.stop(uid)
            cleaned = await adapter.cleanup_names(uid)
            self.assertTrue(cleaned['ok'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

class FreezeFailureSafety(unittest.IsolatedAsyncioTestCase):
    async def test_failed_freeze_never_exports_live_snapshot(self):
        adapter=da.DockerDebugAdapter(binary='must-not-run')
        uid=str(uuid.uuid4());session=da._Session(uid);adapter._sessions[uid]=session
        called=[]
        async def alive(s):return True
        async def freeze(s):return False
        async def export(s):called.append('export');return {},[]
        async def teardown(s):return None
        adapter._workspace_alive=alive;adapter._freeze=freeze;adapter._export_snapshot=export;adapter._teardown=teardown
        result=await adapter.stop(uid)
        self.assertTrue(result['changed_state_unknown']);self.assertIsNone(result['snapshot']);self.assertEqual(called,[])
