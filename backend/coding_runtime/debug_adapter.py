"""Async Docker debugpy DAP session adapter for Harmonika Phase2 (DEBUG-owned).

Scope guard: this module is owned by the Phase2 DEBUG worker only. Root Codex
owns session auth, SQLite, TTL/leases, the REST API and approvals; this adapter
never authenticates callers, never persists anything on the host, and never
touches host PIDs, paths, sockets, networks, GPUs or other processes.

Per debug session this adapter uses (names strictly derived from the canonical
session UUID, prefix ``hms-debug-``, per-resource label
``harmonika.debug=<uuid>``):
- one LOCAL tmpfs Docker volume (32m, uid/gid 10000, never a host bind mount),
- one detached trusted "keeper" container (<= 300s ``sleep`` PID1) that keeps
  the tmpfs volume mounted across import -> session -> export,
- trusted volume_io import/export helper containers (image copy of the
  existing backend/coding_runtime/volume_io.py),
- one interactive main container whose PID1 is the fixed GNU timeout watchdog:
  ``timeout -k 2 <session cap> python3 -m debugpy.adapter`` speaking DAP over
  its stdin/stdout (``docker run -i`` pipes; no ports, no attach). The debug
  target itself is spawned by debugpy INSIDE the network=none namespace on a
  loopback interface internal to that namespace; no host port is ever
  published or required.

Host-side hard guarantees:
- the Docker CLI is the only host interface and is always invoked as a fixed
  argv list via asyncio.create_subprocess_exec (no host shell, no host paths,
  no user-controlled fragments other than the validated canonical UUID);
- every container is read-only-rootfs, network=none, cap-drop=ALL,
  no-new-privileges, pids/memory/cpu limited, uid/gid 10000, private tmpfs
  /tmp, --log-driver=none and nofile/core ulimits, mirroring sandbox.py;
- DAP framing is Content-Length per the Debug Adapter Protocol; inbound
  messages are bounded (<= 512KiB, header <= 4KiB) and a violation tears down
  only this session; responses are correlated by request_seq and async events
  are dispatched with bounded logging and a hard bound (32) on concurrent
  callback tasks (excess dropped and counted, exceptions always retrieved);
- lifecycle: initialize -> launch(program=/workspace/<path>, cwd=/workspace,
  console=internalConsole, stopOnEntry=True, justMyCode=True) -> (initialized)
  -> setBreakpoints* -> configurationDone -> stopped-on-entry, so the target
  is always paused before the caller gains control;
- ``action`` supports status/continue/next/inspect only. inspect fetches
  stackTrace/scopes/variables with hard bounds and redacts obvious sensitive
  variable names. There is NO evaluate, REPL, exec or arbitrary command path
  anywhere in this module;
- ``stop`` is owned and exact: best-effort DAP disconnect, docker pause of the
  OWN main container when a live target could still mutate files, validated
  snapshot export through a separate helper container while the keeper keeps
  the volume mounted, then unpause + rm -f of ONLY this session's containers
  and volume, and reaping of only this adapter's own CLI subprocesses;
- if the main container dies unexpectedly (no terminated event observed) or
  the keeper is lost, the result carries changed_state_unknown=True and no
  snapshot is fabricated;
- session bounds: max session 300s and idle 60s by default (ROOT controls the
  final numbers); an internal reaper freezes/stops only its own expired
  sessions. cleanup_names(UUID) removes exact UUID/label-owned orphan
  resources after a root restart without touching anything else.

Test seam (not reachable from service input): the docker-specific runtime
hooks (``_prepare_workspace``/``_main_argv``/``_freeze``/``_thaw``/
``_export_snapshot``/``_teardown``) are plain methods intended to be
overridden by tests to drive a REAL local ``python3 -m debugpy.adapter``
subprocess in a temporary directory. Service arguments can never select these
paths; production always uses the fixed docker argv below.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

__all__ = [
    'DockerDebugAdapter', 'DebugAdapterError', 'DapProtocolError',
    'DapTransportClosed', 'DapResponseError', 'DapClient', 'DapFrameParser',
    'encode_dap_message', 'validate_session_uuid', 'validate_program_path',
    'validate_breakpoints', 'validate_snapshot_mapping', 'session_names',
    'redact_name', 'bound_text', 'bound_json',
]

IMAGE_DEFAULT = 'harmonika-coding:phase2'
BINARY_DEFAULT = '/usr/bin/docker'
SESSION_PREFIX = 'hms-debug-'
SESSION_LABEL_KEY = 'harmonika.debug'
VOLUME_TMPFS = 'size=32m,uid=10000,gid=10000,mode=0700,nr_inodes=4096,nosuid,nodev'
LOG_DRIVER_FLAGS: Tuple[str, ...] = ('--log-driver=none',)
ULIMIT_FLAGS: Tuple[str, ...] = ('--ulimit', 'nofile=128', '--ulimit', 'core=0')
HARDEN_FLAGS: Tuple[str, ...] = (
    '--read-only',
    '--network=none',
    '--cap-drop=ALL',
    '--security-opt=no-new-privileges',
    '--pids-limit=64',
    '--memory=512m',
    '--memory-swap=512m',
    '--cpus=1',
    '--user=10000:10000',
    '--tmpfs=/tmp:rw,noexec,nosuid,size=16m',
)
DAP_MESSAGE_MAX = 512 * 1024
DAP_HEADER_MAX = 4096
STDERR_LIMIT = 128 * 1024
EVENT_LOG_MAX = 50
SENT_COMMANDS_MAX = 200
SNAPSHOT_MAX_BYTES = 16 * 1024 * 1024
SNAPSHOT_MAX_FILES = 256
SNAPSHOT_MAX_FILE_BYTES = 512 * 1024
PATH_MAX = 240
SESSION_SECONDS_MAX = 300
SESSION_SECONDS_MIN = 5
IDLE_SECONDS_DEFAULT = 60
KEEPER_MARGIN_SECONDS = 15
KEEPER_SECONDS_MAX = 300
HELPER_TIMEOUT_SECONDS = 60
WATCHDOG_KILL_GRACE_SECONDS = 2
INIT_TIMEOUT_SECONDS = 30
CONFIG_DONE_TIMEOUT_SECONDS = 30
DAP_REQUEST_TIMEOUT_SECONDS = 15
LATE_LAUNCH_RESPONSE_TIMEOUT_SECONDS = 120
# Root REST action budget is 20s: continue/next must observe a stop within
# <= 8s and every DAP sub-request stays <= 5s so the whole action fits.
ACTION_WAIT_MAX_SECONDS = 8
LOCATION_TIMEOUT_SECONDS = 5
MAX_CALLBACK_TASKS = 32
STOP_DISCONNECT_TIMEOUT_SECONDS = 5
REAPER_INTERVAL_SECONDS = 2.0
MAX_STACK_FRAMES = 10
MAX_INSPECT_FRAMES = 3
MAX_SCOPES_PER_FRAME = 5
MAX_VARS_PER_SCOPE = 50
VALUE_LIMIT = 200
NAME_LIMIT = 100
MAX_BREAKPOINT_FILES = 20
MAX_BREAKPOINT_LINES = 50
MAX_LINE_NUMBER = 1_000_000
FINISHED_CACHE_MAX = 64
DISCONNECT_GRACE_SECONDS = 1.0
_EVENT_BODY_SLICE = 2000
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_SENSITIVE_RE = re.compile(
    r'(pass(?:word|wd)?|pwd|secret|token|api[-_]?key|access[-_]?key|'
    r'private[-_]?key|secret[-_]?key|auth(?:orization)?|credential|cookie|'
    r'session[-_]?key|bearer|signature)', re.IGNORECASE)
_REDACTED = '<redacted>'


class DebugAdapterError(Exception):
    """Base error; ``code`` is a short stable machine-readable string."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code if not detail else '%s: %s' % (code, detail))
        self.code = code
        self.detail = str(detail)[:500]


class DapProtocolError(DebugAdapterError):
    def __init__(self, detail: str = ''):
        super().__init__('dap_protocol_error', detail)


class DapTransportClosed(DebugAdapterError):
    def __init__(self, detail: str = ''):
        super().__init__('dap_transport_closed', detail)


class DapResponseError(DebugAdapterError):
    def __init__(self, command: str, message: str):
        super().__init__('dap_response_failed', '%s: %s' % (command, message))
        self.command = command


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without asyncio)
# ---------------------------------------------------------------------------

def bound_text(value: Any, limit: int = VALUE_LIMIT) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = repr(value)
    if len(value) > limit:
        return value[:limit] + '…'
    return value


def bound_json(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return '…'
    if isinstance(value, str):
        return bound_text(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 30:
                out['…'] = True
                break
            out[bound_text(key, NAME_LIMIT)] = bound_json(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [bound_json(item, depth + 1) for item in list(value)[:30]]
    return bound_text(value)


def redact_name(name: Any) -> bool:
    if not isinstance(name, str):
        return True
    return bool(_SENSITIVE_RE.search(name))


def validate_session_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise DebugAdapterError('invalid_session_id', 'session_id must be a UUID string')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise DebugAdapterError('invalid_session_id', 'session_id must be a valid UUID')
    canonical = str(parsed)
    if value != canonical:
        raise DebugAdapterError('invalid_session_id',
                                'session_id must be the canonical lowercase UUID form')
    return canonical


def session_names(uid: str) -> Dict[str, str]:
    validate_session_uuid(uid)
    return {
        'container': SESSION_PREFIX + uid,
        'volume': SESSION_PREFIX + 'vol-' + uid,
        'keeper': SESSION_PREFIX + 'k-' + uid,
        'importer': SESSION_PREFIX + 'i-' + uid,
        'exporter': SESSION_PREFIX + 'o-' + uid,
    }


def validate_rel_path(value: Any, what: str = 'path') -> str:
    if not isinstance(value, str):
        raise DebugAdapterError('invalid_path', '%s must be a string' % what)
    if not value or len(value) > PATH_MAX:
        raise DebugAdapterError('invalid_path', '%s length invalid (1..%d)' % (what, PATH_MAX))
    if _CONTROL_RE.search(value):
        raise DebugAdapterError('invalid_path', '%s contains control characters' % what)
    if value.startswith('/'):
        raise DebugAdapterError('invalid_path', '%s must be relative' % what)
    if '\\' in value:
        raise DebugAdapterError('invalid_path', '%s must not contain backslash' % what)
    if value == '.':
        raise DebugAdapterError('invalid_path', '%s must name a file' % what)
    for part in value.split('/'):
        if part in ('', '.', '..'):
            raise DebugAdapterError('invalid_path',
                                    '%s traversal component rejected: %r' % (what, value))
    return value


def validate_program_path(value: Any, snapshot_keys: Sequence[str]) -> str:
    path = validate_rel_path(value, what='program path')
    if not path.endswith('.py'):
        raise DebugAdapterError('invalid_path', 'program path must end with .py')
    if path not in set(snapshot_keys):
        raise DebugAdapterError('path_not_in_snapshot', 'program path not in snapshot: %s' % path[:120])
    return path


def validate_breakpoints(raw: Any, *, allow_empty_lines: bool = False) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_BREAKPOINT_FILES:
        raise DebugAdapterError('invalid_breakpoints',
                                'breakpoints must be a list of at most %d entries' % MAX_BREAKPOINT_FILES)
    clean: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise DebugAdapterError('invalid_breakpoints', 'breakpoint entry must be an object')
        path = validate_rel_path(entry.get('path'), what='breakpoint path')
        if not path.endswith('.py'):
            raise DebugAdapterError('invalid_breakpoints', 'breakpoint path must end with .py')
        lines_raw = entry.get('lines')
        if not isinstance(lines_raw, list) or len(lines_raw) > MAX_BREAKPOINT_LINES:
            raise DebugAdapterError('invalid_breakpoints',
                                    'breakpoint lines must be a list of at most %d ints' % MAX_BREAKPOINT_LINES)
        if not lines_raw and not allow_empty_lines:
            raise DebugAdapterError('invalid_breakpoints',
                                    'breakpoint lines must not be empty')
        lines: List[int] = []
        for line in lines_raw:
            if isinstance(line, bool) or not isinstance(line, int):
                raise DebugAdapterError('invalid_breakpoints', 'breakpoint lines must be integers')
            if not (1 <= line <= MAX_LINE_NUMBER):
                raise DebugAdapterError('invalid_breakpoints',
                                        'breakpoint line out of range: %r' % line)
            if line not in lines:
                lines.append(line)
        clean.append({'path': path, 'lines': sorted(lines)})
    return clean


def validate_snapshot_mapping(snapshot: Any) -> Dict[str, bytes]:
    if not isinstance(snapshot, dict):
        raise DebugAdapterError('invalid_snapshot', 'snapshot must be an object')
    if len(snapshot) > SNAPSHOT_MAX_FILES:
        raise DebugAdapterError('invalid_snapshot',
                                'too many files: %d > %d' % (len(snapshot), SNAPSHOT_MAX_FILES))
    decoded: Dict[str, bytes] = {}
    total = 0
    for rel in sorted(snapshot):
        validate_rel_path(rel, what='snapshot path')
        b64 = snapshot[rel]
        if not isinstance(b64, str):
            raise DebugAdapterError('invalid_snapshot',
                                    'snapshot value must be base64 string: %s' % rel)
        try:
            data = base64.b64decode(b64.encode('ascii'), validate=True)
        except (binascii.Error, ValueError, UnicodeEncodeError):
            raise DebugAdapterError('invalid_snapshot', 'invalid base64 for %s' % rel)
        if len(data) > SNAPSHOT_MAX_FILE_BYTES:
            raise DebugAdapterError('invalid_snapshot',
                                    'file too large: %s (%d > %d)' % (rel, len(data), SNAPSHOT_MAX_FILE_BYTES))
        total += len(data)
        if total > SNAPSHOT_MAX_BYTES:
            raise DebugAdapterError('invalid_snapshot',
                                    'snapshot too large (%d > %d bytes)' % (total, SNAPSHOT_MAX_BYTES))
        decoded[rel] = data
    return decoded


def encode_dap_message(message: Any) -> bytes:
    try:
        body = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise DapProtocolError('message not JSON-serializable: %s' % exc)
    if len(body) > DAP_MESSAGE_MAX:
        raise DapProtocolError('outbound message too large: %d' % len(body))
    return b'Content-Length: %d\r\n\r\n' % len(body) + body


class DapFrameParser:
    """Incremental Content-Length framing parser (DAP)."""

    def __init__(self, max_message: int = DAP_MESSAGE_MAX, header_max: int = DAP_HEADER_MAX):
        self._buf = bytearray()
        self._max = max_message
        self._header_max = header_max

    def feed(self, chunk: bytes) -> List[Dict[str, Any]]:
        self._buf.extend(chunk)
        messages: List[Dict[str, Any]] = []
        while True:
            message, consumed = self._try_parse_one()
            if message is None:
                return messages
            messages.append(message)
            del self._buf[:consumed]

    def _try_parse_one(self) -> Tuple[Optional[Dict[str, Any]], int]:
        head = self._buf.find(b'\r\n\r\n')
        if head < 0:
            if len(self._buf) > self._header_max:
                raise DapProtocolError('DAP header too large')
            return None, 0
        header_bytes = bytes(self._buf[:head])
        if len(header_bytes) + 4 > self._header_max:
            raise DapProtocolError('DAP header too large')
        content_length: Optional[int] = None
        for raw_line in header_bytes.split(b'\r\n'):
            line = raw_line.decode('ascii', 'replace') if isinstance(raw_line, bytes) else raw_line
            if ':' not in line:
                raise DapProtocolError('malformed DAP header line')
            name, value = line.split(':', 1)
            name = name.strip().lower()
            if name == 'content-length':
                value = value.strip()
                if not value.isdigit():
                    raise DapProtocolError('non-numeric Content-Length')
                content_length = int(value)
            # other headers (e.g. Content-Type) are tolerated and ignored
        if content_length is None:
            raise DapProtocolError('missing Content-Length header')
        if content_length <= 0:
            raise DapProtocolError('Content-Length must be positive')
        if content_length > self._max:
            raise DapProtocolError('DAP message too large: %d > %d' % (content_length, self._max))
        body_start = head + 4
        if len(self._buf) - body_start < content_length:
            return None, 0
        body = bytes(self._buf[body_start:body_start + content_length])
        try:
            message = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DapProtocolError('invalid DAP JSON body: %s' % exc)
        if not isinstance(message, dict):
            raise DapProtocolError('DAP message must be an object')
        return message, body_start + content_length


# ---------------------------------------------------------------------------
# DAP client: subprocess transport + framing + request/event correlation
# ---------------------------------------------------------------------------

class DapClient:
    """Owns one debugpy adapter process (or test-attached streams)."""

    def __init__(self, label: str, on_event: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self.label = label
        self.on_event = on_event
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.alive = False
        self.seq = 0
        self.protocol_error: Optional[str] = None
        self.stderr_buf = bytearray()
        self.stderr_truncated = False
        self.events_log: List[Dict[str, Any]] = []
        self.sent_commands: List[str] = []
        self.close_reason: Optional[str] = None
        self._close_callbacks: List[Callable[[str], Any]] = []
        self._pending: Dict[int, asyncio.Future] = {}
        self._event_queues: Dict[str, asyncio.Queue] = {}
        self._write_lock: Optional[asyncio.Lock] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._callback_tasks: Set[asyncio.Task] = set()
        self.dropped_callbacks = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self, argv: Sequence[str], cwd: Optional[str] = None,
                    env: Optional[Dict[str, str]] = None) -> None:
        if not argv:
            raise DebugAdapterError('invalid_argv', 'DAP argv must not be empty')
        try:
            self.proc = await asyncio.create_subprocess_exec(
                argv[0], *argv[1:],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd, env=env)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise DebugAdapterError('spawn_failed', str(exc))
        assert self.proc.stdout is not None and self.proc.stdin is not None \
            and self.proc.stderr is not None
        self.reader = self.proc.stdout
        self.writer = self.proc.stdin
        self._pump_stderr(self.proc.stderr)
        self._begin()

    async def attach(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._begin()

    def _begin(self) -> None:
        self.alive = True
        self._write_lock = asyncio.Lock()
        # pre-create bounded queues so events that arrive before a waiter
        # (e.g. stopped emitted ahead of the configurationDone response) are
        # buffered, never dropped
        for event in ('stopped', 'terminated', 'continued', 'initialized',
                      'exited', 'process', 'thread', 'breakpoint', 'output',
                      'loadedSource', 'module'):
            self._event_queues[event] = asyncio.Queue(maxsize=64)
        self._reader_task = asyncio.ensure_future(self._reader_loop())

    def on_close(self, callback: Callable[[str], Any]) -> None:
        self._close_callbacks.append(callback)

    def _finish(self, reason: str) -> None:
        if not self.alive:
            return
        self.alive = False
        self.close_reason = reason
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DapTransportClosed('adapter transport closed (%s)' % reason))
        self._pending.clear()
        for queue in self._event_queues.values():
            # a waiter must always observe the closure: make room for the
            # sentinel even when the queue is already full (never QueueFull)
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        for callback in list(self._close_callbacks):
            try:
                outcome = callback(reason)
                if asyncio.iscoroutine(outcome):
                    self._spawn_callback(outcome)
            except Exception:
                pass

    async def close(self, kill: bool = True, timeout: float = 5.0) -> None:
        reason = self.close_reason or 'client_close'
        if self.writer is not None:
            try:
                self.writer.close()
            except (OSError, AttributeError):
                pass
        if kill and self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except (ProcessLookupError, OSError):
                pass
        if self.proc is not None:
            try:
                await asyncio.wait_for(self.proc.wait(), timeout)
            except asyncio.TimeoutError:
                try:
                    self.proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        if self.writer is not None and hasattr(self.writer,'wait_closed'):
            try:await asyncio.wait_for(self.writer.wait_closed(),2)
            except (Exception,asyncio.CancelledError):pass
        await self._reap_owned_tasks()
        self._finish(reason)
        # _finish may itself have spawned close-callback tasks; reap those too
        await self._reap_owned_tasks()

    async def _reap_owned_tasks(self) -> None:
        """Cancel and await this client's own tasks (reader, stderr, callbacks)."""
        tasks = [task for task in self._callback_tasks if not task.done()]
        for tracked in (self._reader_task, self._stderr_task):
            if tracked is not None and not tracked.done():
                tasks.append(tracked)
        if not tasks:
            self._callback_tasks.clear()
            return
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(tasks, timeout=2.0)
        for task in pending:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._callback_tasks.clear()

    def _spawn_callback(self, outcome: Any) -> None:
        """Schedule an async callback under a hard bound of MAX_CALLBACK_TASKS.

        Excess callbacks are dropped safely (coroutine closed, never awaited
        unbounded) and counted in ``dropped_callbacks``; finished tasks are
        reaped and their exceptions always retrieved.
        """
        for task in list(self._callback_tasks):
            if task.done():
                self._callback_tasks.discard(task)
        if len(self._callback_tasks) >= MAX_CALLBACK_TASKS:
            self.dropped_callbacks += 1
            if asyncio.iscoroutine(outcome):
                try:
                    outcome.close()
                except Exception:
                    pass
            return
        task = asyncio.ensure_future(outcome)
        self._callback_tasks.add(task)
        task.add_done_callback(self._on_owned_task_done)

    def _on_owned_task_done(self, task: asyncio.Task) -> None:
        self._callback_tasks.discard(task)
        if task.cancelled():
            return
        try:
            # retrieving the exception is the point: a failing callback must
            # never surface as an unretrieved-task-exception warning
            task.exception()
        except asyncio.InvalidStateError:
            pass

    def _pump_stderr(self, stream: asyncio.StreamReader) -> None:
        async def _loop() -> None:
            try:
                while True:
                    chunk = await stream.read(65536)
                    if not chunk:
                        return
                    room = STDERR_LIMIT - len(self.stderr_buf)
                    if room <= 0:
                        self.stderr_truncated = True
                        continue
                    self.stderr_buf.extend(chunk[:room])
                    if len(chunk) > room:
                        self.stderr_truncated = True
            except asyncio.CancelledError:
                raise
            except Exception:
                return
        self._stderr_task = asyncio.ensure_future(_loop())
        self._stderr_task.add_done_callback(self._on_owned_task_done)

    # -- reading -----------------------------------------------------------

    async def _reader_loop(self) -> None:
        parser = DapFrameParser()
        try:
            while True:
                assert self.reader is not None
                chunk = await self.reader.read(65536)
                if not chunk:
                    self._finish('eof')
                    return
                for message in parser.feed(chunk):
                    self._dispatch(message)
        except DapProtocolError as exc:
            self.protocol_error = str(exc)
            self._finish('protocol_error')
        except asyncio.CancelledError:
            self._finish('cancelled')
            raise
        except Exception as exc:
            self._finish('reader_error:%s' % bound_text(exc, 120))

    def _dispatch(self, message: Dict[str, Any]) -> None:
        kind = message.get('type')
        if kind == 'response':
            self._dispatch_response(message)
        elif kind == 'event':
            self._dispatch_event(message)
        else:
            # unknown frame type: tolerate, log bounded
            self.events_log.append({'kind': 'unknown', 'message': bound_json(message)})
            del self.events_log[:-EVENT_LOG_MAX]

    def _dispatch_response(self, message: Dict[str, Any]) -> None:
        request_seq = message.get('request_seq')
        future = self._pending.get(request_seq) if isinstance(request_seq, int) else None
        if future is None or future.done():
            self.events_log.append({'kind': 'unmatched_response',
                                    'request_seq': request_seq,
                                    'command': bound_text(message.get('command'), 60)})
            del self.events_log[:-EVENT_LOG_MAX]
            return
        del self._pending[request_seq]
        if message.get('success') is False:
            future.set_exception(DapResponseError(
                bound_text(message.get('command'), 60),
                bound_text(message.get('message') or 'failed', 300)))
        else:
            future.set_result({'body': message.get('body') or {}})

    def _dispatch_event(self, message: Dict[str, Any]) -> None:
        event = bound_text(message.get('event'), 60)
        body = message.get('body')
        if isinstance(body, dict):
            body.pop('seq', None)
        entry = {'kind': 'event', 'event': event, 'body': bound_json(body)}
        self.events_log.append(entry)
        del self.events_log[:-EVENT_LOG_MAX]
        queue = self._event_queues.get(event)
        if queue is not None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(entry)
        if self.on_event is not None:
            try:
                outcome = self.on_event(entry)
                if asyncio.iscoroutine(outcome):
                    self._spawn_callback(outcome)
            except Exception:
                pass

    # -- writing -----------------------------------------------------------

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    async def request(self, command: str, arguments: Optional[Dict[str, Any]] = None,
                      timeout: float = DAP_REQUEST_TIMEOUT_SECONDS) -> Dict[str, Any]:
        if not self.alive:
            raise DapTransportClosed('adapter not alive')
        if not isinstance(command, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,40}', command):
            raise DebugAdapterError('invalid_command', 'DAP command name invalid')
        if len(self.sent_commands) < SENT_COMMANDS_MAX:
            self.sent_commands.append(command)
        message: Dict[str, Any] = {'seq': self._next_seq(), 'type': 'request',
                                   'command': command}
        if arguments is not None:
            message['arguments'] = arguments
        frame = encode_dap_message(message)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[message['seq']] = future
        try:
            async with self._write_lock:
                assert self.writer is not None
                self.writer.write(frame)
                await self.writer.drain()
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(message['seq'], None)
            raise DebugAdapterError('dap_timeout', 'no response to %s within %.1fs' % (command, timeout))
        except (ConnectionError, OSError, AttributeError) as exc:
            self._pending.pop(message['seq'], None)
            raise DapTransportClosed(bound_text(exc, 200))

    async def wait_event(self, event: str, timeout: float,
                         predicate: Optional[Callable[[Dict[str, Any]], bool]] = None
                         ) -> Dict[str, Any]:
        queue = self._event_queues.get(event)
        if queue is None:
            queue = asyncio.Queue(maxsize=64)
            self._event_queues[event] = queue
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                entry = queue.get_nowait()
            except asyncio.QueueEmpty:
                if not self.alive:
                    raise DapTransportClosed(
                        'adapter closed while waiting for %s' % event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DebugAdapterError('event_timeout',
                                            'no %s event within %.1fs' % (event, timeout))
                try:
                    entry = await asyncio.wait_for(queue.get(), remaining)
                except asyncio.TimeoutError:
                    continue
            if entry is None:
                raise DapTransportClosed('adapter transport closed while waiting for %s' % event)
            if predicate is None or predicate(entry):
                return entry

    async def wait_stopped_or_terminated(self, timeout: float,
                                         extra: Optional[asyncio.Future] = None
                                         ) -> Tuple[str, Dict[str, Any]]:
        stop_task = asyncio.ensure_future(self.wait_event('stopped', timeout))
        term_task = asyncio.ensure_future(self.wait_event('terminated', timeout))
        extra_task: Optional[asyncio.Future] = None
        if extra is not None:
            extra_task = asyncio.ensure_future(asyncio.shield(extra))
        tasks = {stop_task, term_task}
        if extra_task is not None:
            tasks.add(extra_task)
        try:
            while True:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if extra_task is not None and extra_task in done:
                    exc = extra.exception()
                    if exc is not None:
                        # launch itself failed; surface it immediately
                        raise exc
                    # late-but-successful launch response: keep waiting
                    tasks.discard(extra_task)
                    extra_task = None
                    continue
                if stop_task in done:
                    entry = stop_task.result()
                    term_task.cancel()
                    return 'stopped', dict(entry.get('body') or {})
                entry = term_task.result()
                stop_task.cancel()
                return 'terminated', dict(entry.get('body') or {})
        finally:
            for task in (stop_task, term_task):
                if not task.done():
                    task.cancel()
                if task.done() and not task.cancelled():
                    # a finished sibling wait whose result nobody consumes
                    # must never surface as an unretrieved-exception warning
                    task.exception()

    def stderr_text(self, limit: int = 2000) -> str:
        text = self.stderr_buf.decode('utf-8', 'replace')
        if self.stderr_truncated and len(text) >= STDERR_LIMIT - 65536:
            text += '\n…(stderr truncated)'
        return text[-limit:]

    def transport_alive(self) -> bool:
        if self.proc is not None:
            return self.proc.returncode is None and self.alive
        return self.alive


# ---------------------------------------------------------------------------
# Session record
# ---------------------------------------------------------------------------

class _Session:
    __slots__ = ('id', 'names', 'state', 'stop_requested', 'deadline', 'started',
                 'last_activity', 'client', 'snapshot', 'path', 'thread_id',
                 'frame_hint', 'last_stopped_body', 'last_location', 'exit_code',
                 'terminated_observed', 'stop_result', 'lock', 'warnings',
                 'error', 'launch_arguments', 'local_dir', 'frozen', 'clean_exit')

    def __init__(self, uid: str):
        self.id = uid
        self.names = session_names(uid)
        self.state = 'starting'
        self.stop_requested = False
        self.started = time.monotonic()
        self.deadline = self.started + SESSION_SECONDS_MAX
        self.last_activity = self.started
        self.client: Optional[DapClient] = None
        self.snapshot: Dict[str, str] = {}
        self.path = ''
        self.thread_id: Optional[int] = None
        self.frame_hint: Optional[int] = None
        self.last_stopped_body: Dict[str, Any] = {}
        self.last_location: Optional[Dict[str, Any]] = None
        self.exit_code: Optional[int] = None
        self.terminated_observed = False
        self.clean_exit = False
        self.stop_result: Optional[Dict[str, Any]] = None
        self.lock = asyncio.Lock()
        self.warnings: List[str] = []
        self.error: Optional[str] = None
        self.launch_arguments: Dict[str, Any] = {}
        self.local_dir: Optional[str] = None
        self.frozen = False

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def note_warning(self, text: str) -> None:
        self.warnings.append(bound_text(text, 300))
        del self.warnings[:-20]


# ---------------------------------------------------------------------------
# DockerDebugAdapter
# ---------------------------------------------------------------------------

class DockerDebugAdapter:
    """Async debugpy DAP session adapter over owned Docker resources."""

    def __init__(self, image: str = IMAGE_DEFAULT, binary: str = BINARY_DEFAULT,
                 session_max_seconds: int = SESSION_SECONDS_MAX,
                 idle_seconds: int = IDLE_SECONDS_DEFAULT,
                 max_sessions: int = 1,
                 keeper_margin_seconds: int = KEEPER_MARGIN_SECONDS):
        if not isinstance(image, str) or not image:
            raise ValueError('image must be a non-empty string')
        if not isinstance(binary, str) or not binary:
            raise ValueError('binary must be a non-empty string')
        if isinstance(session_max_seconds, bool) or not isinstance(session_max_seconds, int) \
                or not (SESSION_SECONDS_MIN <= session_max_seconds <= SESSION_SECONDS_MAX):
            raise ValueError('session_max_seconds must be within [%d, %d]'
                             % (SESSION_SECONDS_MIN, SESSION_SECONDS_MAX))
        if isinstance(idle_seconds, bool) or not isinstance(idle_seconds, int) or idle_seconds < 5:
            raise ValueError('idle_seconds must be an integer >= 5')
        if isinstance(max_sessions, bool) or not isinstance(max_sessions, int) or max_sessions < 1:
            raise ValueError('max_sessions must be a positive integer')
        self.image = image
        self.binary = binary
        self.session_max_seconds = session_max_seconds
        self.idle_seconds = idle_seconds
        self.max_sessions = max_sessions
        self.keeper_margin_seconds = keeper_margin_seconds
        self._sessions: Dict[str, _Session] = {}
        self._finished: Dict[str, Dict[str, Any]] = {}
        self._reaper_task: Optional[asyncio.Task] = None
        self._semi: Optional[asyncio.Semaphore] = None

    # -- public info ---------------------------------------------------------

    def limits(self) -> Dict[str, Any]:
        return {
            'session_max_seconds': self.session_max_seconds,
            'idle_seconds': self.idle_seconds,
            'max_sessions': self.max_sessions,
            'snapshot_max_bytes': SNAPSHOT_MAX_BYTES,
            'snapshot_max_files': SNAPSHOT_MAX_FILES,
            'snapshot_max_file_bytes': SNAPSHOT_MAX_FILE_BYTES,
            'dap_message_max_bytes': DAP_MESSAGE_MAX,
            'stack_frames_max': MAX_STACK_FRAMES,
            'variables_per_scope_max': MAX_VARS_PER_SCOPE,
            'value_char_limit': VALUE_LIMIT,
            'breakpoints_max_files': MAX_BREAKPOINT_FILES,
            'breakpoints_max_lines_per_file': MAX_BREAKPOINT_LINES,
            'keeper_seconds_max': KEEPER_SECONDS_MAX,
            'container_label': SESSION_LABEL_KEY,
            'container': {'read_only': True, 'network': 'none', 'cap_drop': 'ALL',
                          'no_new_privileges': True, 'pids_limit': 64, 'memory': '512m',
                          'cpus': 1, 'user': '10000:10000', 'log_driver': 'none'},
            'volume': {'type': 'tmpfs', 'opts': VOLUME_TMPFS},
            'evaluate': False,
        }

    def session_state(self, session_id: str) -> Dict[str, Any]:
        uid = validate_session_uuid(session_id)
        session = self._sessions.get(uid)
        if session is None:
            return {'session_id': uid, 'state': 'unknown'}
        return {'session_id': uid, 'state': session.state,
                'path': session.path,
                'remaining_seconds': round(max(0.0, session.remaining()), 3)}

    async def capabilities(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            'binary': self.binary,
            'image': self.image,
            'docker_available': False,
            'image_available': False,
            'debugpy_in_image': None,
            'debugpy_version': None,
            'ready': False,
            'server_version': None,
            'error': None,
        }
        try:
            rc, so, se = await self._run_captured(
                [self.binary, 'version', '--format', '{{.Server.Version}}'], timeout=10)
        except DebugAdapterError as exc:
            out['error'] = 'docker unavailable: %s' % exc
            return out
        if rc != 0:
            out['error'] = 'docker version failed: %s' % se.decode('utf-8', 'replace')[:300]
            return out
        out['docker_available'] = True
        out['server_version'] = so.decode('utf-8', 'replace').strip()
        try:
            rc2, _, se2 = await self._run_captured(
                [self.binary, 'image', 'inspect', self.image], timeout=15)
        except DebugAdapterError as exc:
            out['error'] = str(exc)
            return out
        out['image_available'] = rc2 == 0
        if rc2 != 0:
            out['error'] = 'image %s not present: %s' % (
                self.image, se2.decode('utf-8', 'replace')[:300])
            return out
        probe = [self.binary, 'run', '--rm', '--log-driver=none']
        probe += list(HARDEN_FLAGS)
        probe += [self.image, 'python3', '-c',
                  'import debugpy,sys;sys.stdout.write(str(debugpy.__version__))']
        try:
            rc3, so3, _ = await self._run_captured(probe, timeout=30)
        except DebugAdapterError as exc:
            out['error'] = 'debugpy probe failed: %s' % exc
            return out
        if rc3 == 0:
            out['debugpy_in_image'] = True
            out['debugpy_version'] = so3.decode('utf-8', 'replace').strip()[:40]
        else:
            out['debugpy_in_image'] = False
            out['error'] = 'debugpy not importable in image'
        out['ready'] = out['docker_available'] and out['image_available'] and out['debugpy_in_image'] is True
        return out

    # -- low-level docker ----------------------------------------------------

    async def _run_captured(self, argv: List[str], *, input_bytes: Optional[bytes] = None,
                            timeout: Optional[float] = None) -> Tuple[Optional[int], bytes, bytes]:
        stdin = asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL
        try:
            proc = await asyncio.create_subprocess_exec(
                argv[0], *argv[1:], stdin=stdin,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise DebugAdapterError('docker_cli_unavailable', str(exc))
        try:
            out, err = await asyncio.wait_for(proc.communicate(input_bytes), timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                pass
            raise DebugAdapterError('docker_cli_timeout', ' '.join(argv[:3]))
        return proc.returncode, out or b'', err or b''

    def _base_run_argv(self, name: str, volume: str, uid: str, *,
                       interactive: bool = False, detached: bool = False) -> List[str]:
        argv = [self.binary, 'run', '--rm', '--name', name]
        if interactive:
            argv.append('--interactive')
        if detached:
            argv.append('-d')
        argv += ['-v', '%s:/workspace:nocopy' % volume, '-w', '/workspace']
        argv += list(HARDEN_FLAGS)
        argv += list(LOG_DRIVER_FLAGS)
        argv += list(ULIMIT_FLAGS)
        argv += ['--label', '%s=%s' % (SESSION_LABEL_KEY, uid)]
        argv.append(self.image)
        return argv

    # -- runtime hooks (production = docker; tests override only these) ------

    def _workspace_prefix(self, session: _Session) -> str:
        return '/workspace'

    def _program_path(self, session: _Session, rel: str) -> str:
        return self._workspace_prefix(session) + '/' + rel

    def _launch_arguments(self, session: _Session, rel: str) -> Dict[str, Any]:
        return {
            'program': self._program_path(session, rel),
            'cwd': self._workspace_prefix(session),
            'console': 'internalConsole',
            'stopOnEntry': True,
            'justMyCode': True,
        }

    def _main_argv(self, session: _Session) -> List[str]:
        cap = int(max(1, session.remaining()))
        argv = self._base_run_argv(session.names['container'], session.names['volume'],
                                   session.id, interactive=True)
        argv += ['timeout', '-k', str(WATCHDOG_KILL_GRACE_SECONDS), str(cap),
                 'python3', '-m', 'debugpy.adapter']
        return argv

    async def _prepare_workspace(self, session: _Session, payload: bytes) -> None:
        names = session.names
        rc, _, err = await self._run_captured([self.binary, 'volume', 'create',
                                               '--driver', 'local',
                                               '--label', '%s=%s' % (SESSION_LABEL_KEY, session.id),
                                               '--name', names['volume'],
                                               '--opt', 'type=tmpfs',
                                               '--opt', 'device=tmpfs',
                                               '--opt', 'o=' + VOLUME_TMPFS], timeout=15)
        if rc != 0:
            raise DebugAdapterError('volume_create_failed', err.decode('utf-8', 'replace')[:300])
        keeper_argv = self._base_run_argv(names['keeper'], names['volume'], session.id,
                                          detached=True)
        keeper_seconds = int(min(max(1, session.remaining() + self.keeper_margin_seconds),
                                 KEEPER_SECONDS_MAX))
        keeper_argv += ['sleep', str(keeper_seconds)]
        rc, _, err = await self._run_captured(keeper_argv, timeout=15)
        if rc != 0:
            raise DebugAdapterError('keeper_failed', err.decode('utf-8', 'replace')[:300])
        import_argv = self._base_run_argv(names['importer'], names['volume'], session.id,
                                          interactive=True)
        import_argv += ['timeout', '-k', str(WATCHDOG_KILL_GRACE_SECONDS),
                        str(HELPER_TIMEOUT_SECONDS),
                        'python3', '/opt/harmonika/volume_io.py', '--import']
        rc, _, err = await self._run_captured(import_argv, input_bytes=payload,
                                              timeout=HELPER_TIMEOUT_SECONDS + 10)
        if rc != 0:
            raise DebugAdapterError('import_failed', err.decode('utf-8', 'replace')[:300])

    async def _workspace_alive(self, session: _Session) -> bool:
        try:
            rc, out, _ = await self._run_captured(
                [self.binary, 'inspect', '-f', '{{.State.Running}}', session.names['keeper']],
                timeout=10)
        except DebugAdapterError:
            return False
        return rc == 0 and out.decode('utf-8', 'replace').strip().lower() == 'true'

    async def _freeze(self, session: _Session) -> bool:
        rc, _, _ = await self._run_captured([self.binary, 'pause', session.names['container']],
                                            timeout=10)
        session.frozen = rc == 0
        if session.frozen:return True
        # A stopped container has no writers; a failed pause of a still-running
        # or unobservable target must never publish an unfrozen snapshot.
        rc,raw,_=await self._run_captured([self.binary,'inspect','-f','{{.State.Running}}',session.names['container']],timeout=5)
        return rc==0 and raw.decode('utf-8','replace').strip().lower()=='false'

    async def _thaw(self, session: _Session) -> None:
        if not session.frozen:
            return
        try:
            await self._run_captured([self.binary, 'unpause', session.names['container']],
                                     timeout=10)
        except DebugAdapterError:
            pass
        session.frozen = False

    def _validate_export_payload(self, stdout: bytes) -> Tuple[Dict[str, str], List[str]]:
        try:
            payload = json.loads(stdout.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DebugAdapterError('export_invalid', 'invalid JSON: %s' % exc)
        if not isinstance(payload, dict) or not isinstance(payload.get('snapshot'), dict):
            raise DebugAdapterError('export_invalid', 'payload missing snapshot mapping')
        mapping = payload['snapshot']
        validate_snapshot_mapping(mapping)
        warnings: List[str] = []
        raw_warnings = payload.get('warnings')
        if isinstance(raw_warnings, list):
            for line in raw_warnings[:20]:
                if isinstance(line, str) and line.strip():
                    warnings.append(bound_text('export: %s' % line, 300))
        return {rel: mapping[rel] for rel in sorted(mapping)}, warnings

    async def _export_snapshot(self, session: _Session) -> Tuple[Optional[Dict[str, str]], List[str]]:
        names = session.names
        export_argv = self._base_run_argv(names['exporter'], names['volume'], session.id)
        export_argv += ['timeout', '-k', str(WATCHDOG_KILL_GRACE_SECONDS),
                        str(HELPER_TIMEOUT_SECONDS),
                        'python3', '/opt/harmonika/volume_io.py', '--export']
        rc, out, err = await self._run_captured(export_argv,
                                                timeout=HELPER_TIMEOUT_SECONDS + 10)
        warnings: List[str] = []
        for line in err.decode('utf-8', 'replace').splitlines()[:20]:
            if line.strip():
                warnings.append(bound_text('export: %s' % line, 300))
        if rc != 0:
            warnings.append('export helper exit=%s' % rc)
            return None, warnings
        snapshot, payload_warnings = self._validate_export_payload(out)
        warnings.extend(payload_warnings)
        return snapshot, warnings

    async def _teardown(self, session: _Session) -> None:
        names = session.names
        await self._thaw(session)
        for key in ('container', 'keeper', 'importer', 'exporter'):
            try:
                await self._run_captured([self.binary, 'rm', '-f', names[key]], timeout=15)
            except DebugAdapterError:
                pass
        try:
            await self._run_captured([self.binary, 'volume', 'rm', '-f', names['volume']],
                                     timeout=15)
        except DebugAdapterError:
            pass

    # -- sessions ------------------------------------------------------------

    def _sem(self) -> asyncio.Semaphore:
        if self._semi is None:
            self._semi = asyncio.Semaphore(self.max_sessions)
        return self._semi

    def _ensure_reaper(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.ensure_future(self._reaper_loop())

    async def _reaper_loop(self) -> None:
        try:
            while self._sessions:
                await asyncio.sleep(REAPER_INTERVAL_SECONDS)
                await self._reap_cycle()
        except asyncio.CancelledError:
            raise

    def _spawn_reaper_stop(self, uid: str, reason: str, status: str) -> None:
        task = asyncio.ensure_future(self.stop(uid, reason=reason, status=status))
        task.add_done_callback(self._retrieve_task_exception)

    @staticmethod
    def _retrieve_task_exception(task: 'asyncio.Task') -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.InvalidStateError:
            pass

    async def _reap_cycle(self) -> None:
        now = time.monotonic()
        for uid, session in list(self._sessions.items()):
            if session.stop_requested or session.stop_result is not None:
                continue
            if session.state in ('stopping', 'stopped'):
                continue
            client = session.client
            unexpected = (client is not None and not client.transport_alive()
                          and not session.terminated_observed)
            if unexpected:
                session.state = 'failed'
                session.error = 'target_lost'
                self._spawn_reaper_stop(uid, 'target_lost', 'failed')
                continue
            if session.remaining() <= 0:
                self._spawn_reaper_stop(uid, 'session_timeout', 'timeout')
                continue
            if now - session.last_activity > self.idle_seconds:
                self._spawn_reaper_stop(uid, 'idle_timeout', 'idle_timeout')

    async def start(self, snapshot: Any, args: Any, session_id: str,
                    on_event: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
        started = time.monotonic()
        def fail(code: str, detail: str, state: str = 'failed') -> Dict[str, Any]:
            return {'session_id': session_id if isinstance(session_id, str) else None,
                    'state': state, 'status': 'failed', 'error': code,
                    'reason': bound_text(detail, 300),
                    'duration_ms': int((time.monotonic() - started) * 1000),
                    'limits': self.limits()}
        try:
            uid = validate_session_uuid(session_id)
        except DebugAdapterError as exc:
            return fail(exc.code, exc.detail)
        try:
            decoded = validate_snapshot_mapping(snapshot)
            if not isinstance(args, dict):
                raise DebugAdapterError('invalid_request', 'args must be an object')
            allowed = {'path', 'breakpoints', 'timeout_seconds'}
            if set(args) - allowed:
                raise DebugAdapterError('invalid_request',
                                        'unknown args keys: %s' % sorted(set(args) - allowed))
            rel = validate_program_path(args.get('path'), decoded.keys())
            breakpoints = validate_breakpoints(args.get('breakpoints'))
            timeout_seconds = args.get('timeout_seconds', self.session_max_seconds)
            if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
                raise DebugAdapterError('invalid_request', 'timeout_seconds must be a number')
            timeout_seconds = float(timeout_seconds)
            if not (SESSION_SECONDS_MIN <= timeout_seconds <= self.session_max_seconds):
                raise DebugAdapterError('invalid_request',
                                        'timeout_seconds must be within [%d, %d]'
                                        % (SESSION_SECONDS_MIN, self.session_max_seconds))
        except DebugAdapterError as exc:
            return fail(exc.code, exc.detail)
        if uid in self._sessions and self._sessions[uid].stop_result is None:
            return fail('duplicate_session', 'session already active')
        if len(self._sessions) >= self.max_sessions:
            return fail('session_limit', 'at most %d concurrent debug sessions' % self.max_sessions)

        session = _Session(uid)
        session.deadline = time.monotonic() + timeout_seconds
        session.snapshot = {key: base64.b64encode(decoded[key]).decode('ascii')
                            for key in sorted(decoded)}
        session.path = rel
        self._sessions[uid] = session
        self._ensure_reaper()
        payload = json.dumps({'snapshot': session.snapshot}).encode('utf-8')
        try:
            async with self._sem():
                await self._prepare_workspace(session, payload)
                if not await self._workspace_alive(session):
                    raise DebugAdapterError('keeper_lost', 'keeper not running after import')
                client = DapClient(uid, on_event=self._wrap_event_callback(session, on_event))
                session.client = client
                client.on_close(lambda reason, s=session: self._on_transport_closed(s, reason))
                await client.start(self._main_argv(session), cwd=None)
                await self._dap_configure(session, client, breakpoints)
            if session.state != 'terminated':
                # only an actually paused target reports paused; if configure
                # observed the target terminate, that observation stands
                session.state = 'paused'
            session.touch()
            return {
                'session_id': uid,
                'state': session.state,
                'status': 'ok',
                'path': rel,
                'stop_reason': 'entry' if session.state == 'paused' else 'terminated',
                'breakpoints': breakpoints,
                'duration_ms': int((time.monotonic() - started) * 1000),
                'limits': self.limits(),
            }
        except DebugAdapterError as exc:
            session.state = 'failed'
            session.error = exc.code
            session.note_warning('%s: %s' % (exc.code, exc.detail))
            await self._abort_session(session, exc)
            return fail(exc.code, exc.detail)
        except asyncio.CancelledError:
            session.state = 'failed'
            await self._abort_session(session, DebugAdapterError('cancelled', 'start cancelled'))
            raise

    def _wrap_event_callback(self, session: _Session,
                             on_event: Optional[Callable[[Dict[str, Any]], Any]]):
        if on_event is None:
            return None
        async def _cb(entry: Dict[str, Any]) -> None:
            outcome = on_event({'session_id': session.id, 'event': entry.get('event'),
                                'body': entry.get('body')})
            if asyncio.iscoroutine(outcome):
                await outcome
        return _cb

    def _on_transport_closed(self, session: _Session, reason: str) -> None:
        if session.stop_requested or session.stop_result is not None:
            return
        session.note_warning('transport closed: %s' % reason)
        if session.terminated_observed:
            session.state = 'terminated'
        elif session.state not in ('failed', 'stopping'):
            session.state = 'failed'
            session.error = 'target_lost'

    async def _abort_session(self, session: _Session, exc: DebugAdapterError) -> None:
        try:
            if session.client is not None:
                await session.client.close(kill=True)
        except Exception:
            pass
        try:
            await self._teardown(session)
        except Exception:
            pass
        self._finished[session.id] = {
            'session_id': session.id, 'ok': True, 'state': 'stopped', 'status': 'failed',
            'reason': exc.code, 'changed_state_unknown': True, 'snapshot': None,
        }
        if len(self._finished) > FINISHED_CACHE_MAX:
            for key in sorted(self._finished.keys())[:-FINISHED_CACHE_MAX]:
                self._finished.pop(key, None)
        self._sessions.pop(session.id, None)

    async def _dap_configure(self, session: _Session, client: DapClient,
                             breakpoints: List[Dict[str, Any]]) -> None:
        remaining = max(1.0, session.remaining())
        init_timeout = min(INIT_TIMEOUT_SECONDS, remaining)
        await client.request('initialize', {
            'adapterID': 'harmonika-debug',
            'clientID': 'harmonika-tui',
            'clientName': 'Harmonika Coding',
            'locale': 'en',
            'pathFormat': 'path',
            'supportsVariableType': False,
            'supportsRunInTerminalRequest': False,
        }, timeout=init_timeout)
        session.launch_arguments = self._launch_arguments(session, session.path)
        # debugpy answers `launch` only after the debuggee connects; the
        # contract keeps launch pending across initialized -> setBreakpoints
        # -> configurationDone, so the response is awaited in the background
        # and a launch failure surfaces through the joint wait below.
        launch_task = asyncio.ensure_future(client.request(
            'launch', session.launch_arguments,
            timeout=LATE_LAUNCH_RESPONSE_TIMEOUT_SECONDS))
        try:
            await client.wait_event('initialized',
                                    timeout=min(INIT_TIMEOUT_SECONDS, remaining))
            for entry in breakpoints:
                await client.request('setBreakpoints', {
                    'source': {'path': self._program_path(session, entry['path'])},
                    'lines': entry['lines'],
                    'breakpoints': [{'line': line} for line in entry['lines']],
                }, timeout=min(DAP_REQUEST_TIMEOUT_SECONDS, remaining))
            await client.request('configurationDone', {},
                                 timeout=min(CONFIG_DONE_TIMEOUT_SECONDS, remaining))
            outcome, body = await client.wait_stopped_or_terminated(
                min(ACTION_WAIT_MAX_SECONDS, max(1.0, session.remaining())),
                extra=launch_task)
        except BaseException:
            if not launch_task.done():
                launch_task.cancel()
            raise
        if not launch_task.done():
            launch_task.cancel()
        if outcome == 'terminated':
            session.state = 'terminated'
            session.terminated_observed = True
            session.clean_exit = True
            return
        session.state = 'paused'
        session.last_stopped_body = body
        thread_id = body.get('threadId')
        session.thread_id = thread_id if isinstance(thread_id, int) else None

    # -- breakpoint mutation ---------------------------------------------------

    async def set_breakpoints(self, session_id: str, path: Any, lines: Any) -> Dict[str, Any]:
        try:
            uid = validate_session_uuid(session_id)
        except DebugAdapterError as exc:
            return {'session_id': None, 'ok': False, 'error': exc.code,
                    'reason': exc.detail}
        try:
            if not isinstance(path, str) or not path.endswith('.py'):
                raise DebugAdapterError('invalid_path', 'breakpoint path must end with .py')
            entry = validate_breakpoints([{'path': path, 'lines': lines}],
                                         allow_empty_lines=(lines == []))[0]
        except DebugAdapterError as exc:
            return {'session_id': uid, 'ok': False, 'error': exc.code, 'reason': exc.detail}
        session = self._sessions.get(uid)
        if session is None or session.client is None:
            return {'session_id': uid, 'ok': False, 'error': 'unknown_session'}
        if session.state not in ('paused', 'running'):
            return {'session_id': uid, 'ok': False, 'error': 'invalid_state',
                    'state': session.state}
        timeout = min(DAP_REQUEST_TIMEOUT_SECONDS, max(1.0, session.remaining()))
        try:
            response = await session.client.request('setBreakpoints', {
                'source': {'path': self._program_path(session, entry['path'])},
                'lines': entry['lines'],
                'breakpoints': [{'line': line} for line in entry['lines']],
            }, timeout=timeout)
        except DebugAdapterError as exc:
            session.note_warning('set_breakpoints: %s' % exc.code)
            return {'session_id': uid, 'ok': False, 'error': exc.code, 'reason': exc.detail}
        session.touch()
        body = response.get('body') or {}
        raw = body.get('breakpoints') if isinstance(body.get('breakpoints'), list) else []
        reported = []
        for item in raw[:MAX_BREAKPOINT_LINES]:
            if isinstance(item, dict):
                reported.append({'line': item.get('line'), 'verified': bool(item.get('verified'))})
        return {'session_id': uid, 'ok': True, 'path': entry['path'],
                'breakpoints': reported, 'state': session.state}

    # -- actions ----------------------------------------------------------------

    async def action(self, session_id: str, action: Any, args: Any = None) -> Dict[str, Any]:
        try:
            uid = validate_session_uuid(session_id)
        except DebugAdapterError as exc:
            return {'session_id': None, 'ok': False, 'error': exc.code,
                    'reason': exc.detail}
        if action not in ('status', 'continue', 'next', 'inspect'):
            return {'session_id': uid, 'ok': False, 'error': 'invalid_action',
                    'action': action if isinstance(action, str) else None}
        session = self._sessions.get(uid)
        if session is None or session.client is None:
            return {'session_id': uid, 'ok': False, 'error': 'unknown_session'}
        async with session.lock:
            return await self._action_locked(session, action, args)

    async def _action_locked(self, session: _Session, action: str,
                             args: Any) -> Dict[str, Any]:
        client = session.client
        assert client is not None
        base = {'session_id': session.id, 'action': action, 'state': session.state,
                'remaining_seconds': round(max(0.0, session.remaining()), 3)}
        if session.state in ('terminated', 'failed', 'stopping', 'stopped'):
            if action == 'status':
                base.update({'ok': True, 'paused': False,
                             'terminated': session.terminated_observed,
                             'exit_code': session.exit_code, 'error': session.error,
                             'location': session.last_location})
                return base
            return dict(base, ok=False, error='invalid_state', state=session.state)
        if action == 'status':
            return await self._status_action(session, base)
        if session.state != 'paused':
            return dict(base, ok=False, error='not_paused', state=session.state)
        if action in ('continue', 'next'):
            return await self._resume_action(session, action, base)
        return await self._inspect_action(session, base)

    async def _current_thread_id(self, session: _Session) -> Optional[int]:
        if session.thread_id is not None:
            return session.thread_id
        client = session.client
        assert client is not None
        try:
            response = await client.request('threads', {},
                                            timeout=min(LOCATION_TIMEOUT_SECONDS,
                                                        DAP_REQUEST_TIMEOUT_SECONDS,
                                                        max(1.0, session.remaining())))
        except DebugAdapterError:
            return None
        threads = (response.get('body') or {}).get('threads')
        if isinstance(threads, list) and threads and isinstance(threads[0], dict):
            tid = threads[0].get('id')
            if isinstance(tid, int):
                session.thread_id = tid
                return tid
        return None

    async def _fetch_location(self, session: _Session) -> Optional[Dict[str, Any]]:
        client = session.client
        assert client is not None
        tid = await self._current_thread_id(session)
        if tid is None:
            return None
        try:
            response = await client.request('stackTrace', {
                'threadId': tid, 'startFrame': 0, 'levels': 1},
                timeout=min(LOCATION_TIMEOUT_SECONDS, DAP_REQUEST_TIMEOUT_SECONDS,
                            max(1.0, session.remaining())))
        except DebugAdapterError:
            return None
        frames = (response.get('body') or {}).get('stackFrames')
        if isinstance(frames, list) and frames and isinstance(frames[0], dict):
            frame = frames[0]
            source = frame.get('source') if isinstance(frame.get('source'), dict) else {}
            location = {
                'path': bound_text(source.get('path'), PATH_MAX),
                'line': frame.get('line'),
                'column': frame.get('column'),
                'name': bound_text(frame.get('name'), NAME_LIMIT),
            }
            session.last_location = location
            return location
        return None

    async def _status_action(self, session: _Session, base: Dict[str, Any]) -> Dict[str, Any]:
        location: Optional[Dict[str, Any]] = None
        if session.state == 'paused':
            try:
                location = await self._fetch_location(session)
            except DebugAdapterError as exc:
                session.note_warning('status: %s' % exc.code)
        session.touch()
        return dict(base, ok=True, paused=session.state == 'paused',
                    terminated=session.terminated_observed,
                    exit_code=session.exit_code, location=location or session.last_location)

    async def _resume_action(self, session: _Session, action: str,
                             base: Dict[str, Any]) -> Dict[str, Any]:
        client = session.client
        assert client is not None
        tid = await self._current_thread_id(session)
        if tid is None:
            return dict(base, ok=False, error='no_thread')
        timeout = min(DAP_REQUEST_TIMEOUT_SECONDS, max(1.0, session.remaining()))
        try:
            if action == 'continue':
                await client.request('continue', {'threadId': tid}, timeout=timeout)
            else:
                await client.request('next', {'threadId': tid}, timeout=timeout)
        except DebugAdapterError as exc:
            session.note_warning('%s: %s' % (action, exc.code))
            return dict(base, ok=False, error=exc.code, reason=exc.detail)
        session.state = 'running'
        # root action budget is 20s: wait at most ACTION_WAIT_MAX_SECONDS (8s)
        # for the stop/terminated observation, then report what was observed
        wait = min(ACTION_WAIT_MAX_SECONDS, max(1.0, session.remaining()))
        outcome: Optional[str] = None
        body: Dict[str, Any] = {}
        wait_timeout = False
        try:
            outcome, body = await client.wait_stopped_or_terminated(wait)
        except DebugAdapterError as exc:
            session.note_warning('%s wait: %s' % (action, exc.code))
            if isinstance(exc, DapTransportClosed):
                session.state = 'terminated' if session.terminated_observed else 'failed'
                return dict(base, ok=False, error=exc.code, state=session.state)
            wait_timeout = True
            # truthful final look: the event may have landed just past the
            # deadline; report paused/terminated only if actually observed
            try:
                outcome, body = await client.wait_stopped_or_terminated(0)
            except DapTransportClosed:
                session.state = 'terminated' if session.terminated_observed else 'failed'
                return dict(base, ok=False, error='dap_transport_closed',
                            state=session.state)
            except DebugAdapterError:
                outcome = None
        if outcome == 'terminated':
            session.state = 'terminated'
            session.terminated_observed = True
            session.clean_exit = True
            session.touch()
            extra = {'waited': True, 'wait_timeout': True} if wait_timeout else {}
            return dict(base, ok=True, state='terminated', terminated=True, **extra)
        if outcome == 'stopped':
            session.state = 'paused'
            session.last_stopped_body = body
            thread_id = body.get('threadId')
            session.thread_id = thread_id if isinstance(thread_id, int) else None
            reason = bound_text(body.get('reason'), 40)
            location = None
            try:
                location = await self._fetch_location(session)
            except DebugAdapterError as exc:
                session.note_warning('location: %s' % exc.code)
            session.touch()
            extra = {'waited': True, 'wait_timeout': True} if wait_timeout else {}
            return dict(base, ok=True, state='paused', stopped_reason=reason,
                        location=location, **extra)
        # nothing observed: the target is (as far as we observed) still running
        return dict(base, ok=True, state='running', waited=True, wait_timeout=True)

    async def _inspect_action(self, session: _Session, base: Dict[str, Any]) -> Dict[str, Any]:
        client = session.client
        assert client is not None
        remaining = lambda: min(DAP_REQUEST_TIMEOUT_SECONDS, max(1.0, session.remaining()))
        tid = await self._current_thread_id(session)
        if tid is None:
            return dict(base, ok=False, error='no_thread')
        try:
            stack = await client.request('stackTrace', {
                'threadId': tid, 'startFrame': 0, 'levels': MAX_STACK_FRAMES},
                timeout=remaining())
        except DebugAdapterError as exc:
            session.note_warning('inspect: %s' % exc.code)
            return dict(base, ok=False, error=exc.code)
        frames_raw = (stack.get('body') or {}).get('stackFrames')
        frames: List[Dict[str, Any]] = []
        if isinstance(frames_raw, list):
            for frame in frames_raw[:MAX_STACK_FRAMES]:
                if not isinstance(frame, dict):
                    continue
                source = frame.get('source') if isinstance(frame.get('source'), dict) else {}
                frames.append({
                    'id': frame.get('id'),
                    'name': bound_text(frame.get('name'), NAME_LIMIT),
                    'path': bound_text(source.get('path'), PATH_MAX),
                    'line': frame.get('line'),
                    'column': frame.get('column'),
                })
        if frames:
            session.last_location = {'path': frames[0]['path'], 'line': frames[0]['line'],
                                     'column': frames[0]['column'], 'name': frames[0]['name']}
        variables: Dict[str, Any] = {}
        truncated = False
        for frame in frames[:MAX_INSPECT_FRAMES]:
            frame_id = frame.get('id')
            if not isinstance(frame_id, int):
                continue
            try:
                scopes_resp = await client.request('scopes', {'frameId': frame_id},
                                                   timeout=remaining())
            except DebugAdapterError as exc:
                session.note_warning('scopes: %s' % exc.code)
                break
            scopes_raw = (scopes_resp.get('body') or {}).get('scopes')
            if not isinstance(scopes_raw, list):
                continue
            frame_vars: Dict[str, Any] = {}
            for scope in scopes_raw[:MAX_SCOPES_PER_FRAME]:
                if not isinstance(scope, dict) or scope.get('expensive'):
                    continue
                ref = scope.get('variablesReference')
                if not isinstance(ref, int):
                    continue
                try:
                    vars_resp = await client.request('variables', {
                        'variablesReference': ref, 'start': 0, 'count': MAX_VARS_PER_SCOPE},
                        timeout=remaining())
                except DebugAdapterError as exc:
                    session.note_warning('variables: %s' % exc.code)
                    break
                raw_vars = (vars_resp.get('body') or {}).get('variables')
                scope_vars: Dict[str, Any] = {}
                if isinstance(raw_vars, list):
                    if len(raw_vars) > MAX_VARS_PER_SCOPE:
                        truncated = True
                    for item in raw_vars[:MAX_VARS_PER_SCOPE]:
                        if not isinstance(item, dict):
                            continue
                        name = bound_text(item.get('name'), NAME_LIMIT)
                        if name in scope_vars:
                            continue
                        value = item.get('value')
                        redacted = redact_name(name)
                        scope_vars[name] = {
                            'value': _REDACTED if redacted else bound_text(value),
                            'type': bound_text(item.get('type'), 60),
                            'redacted': redacted,
                        }
                frame_vars[bound_text(scope.get('name'), 60)] = scope_vars
            variables['frame:%s' % bound_text(frame['name'], 60)] = frame_vars
        session.touch()
        return dict(base, ok=True, state='paused', thread_id=tid,
                    frames=frames, variables=variables, truncated=truncated,
                    no_evaluate=True)

    # -- owned stop / cleanup -----------------------------------------------------

    async def stop(self, session_id: str, *, reason: str = 'stop_requested',
                   status: str = 'stopped') -> Dict[str, Any]:
        try:
            uid = validate_session_uuid(session_id)
        except DebugAdapterError as exc:
            return {'session_id': session_id if isinstance(session_id, str) else None,
                    'ok': False, 'error': exc.code}
        session = self._sessions.get(uid)
        if session is None:
            cached = self._finished.get(uid)
            if cached is not None:
                return dict(cached)
            return {'session_id': uid, 'ok': False, 'error': 'unknown_session',
                    'state': 'unknown'}
        async with session.lock:
            if session.stop_result is not None:
                return dict(session.stop_result)
            session.stop_requested = True
            session.state = 'stopping'
            result = await self._stop_locked(session, reason, status)
            session.stop_result = result
            self._sessions.pop(uid, None)
            self._finished[uid] = dict(result)
            if len(self._finished) > FINISHED_CACHE_MAX:
                for key in sorted(self._finished.keys())[:-FINISHED_CACHE_MAX]:
                    self._finished.pop(key, None)
            return dict(result)

    async def _stop_locked(self, session: _Session, reason: str,
                           status: str) -> Dict[str, Any]:
        client = session.client
        changed_unknown = False
        snapshot: Optional[Dict[str, str]] = None
        warnings: List[str] = list(session.warnings)

        # FIRST test for an unexpected PRIOR death: a transport that already
        # died on its own (no terminated event observed) means we cannot trust
        # the workspace, no matter what we do below.
        unexpected_death = (client is not None and not client.transport_alive()
                            and not session.terminated_observed)
        if unexpected_death:
            changed_unknown = True
            warnings.append('main container died unexpectedly; snapshot suppressed')
        else:
            # best-effort graceful DAP disconnect (terminate debuggee). The
            # transport closing as a RESULT of this requested disconnect is an
            # expected outcome and must never be reclassified as an unexpected
            # death: the snapshot decision was already made above.
            if client is not None and client.transport_alive():
                try:
                    await client.request('disconnect', {'terminateDebuggee': True},
                                         timeout=min(STOP_DISCONNECT_TIMEOUT_SECONDS,
                                                     max(0.5, session.remaining())))
                except DebugAdapterError:
                    pass
                await asyncio.sleep(min(DISCONNECT_GRACE_SECONDS, 0.5))

        if not changed_unknown:
            # freeze before exporting whenever the session is not already
            # untrusted, even if the DAP transport closed during disconnect
            # (the container/target may still be alive independently)
            if not await self._workspace_alive(session):
                changed_unknown = True
                warnings.append('keeper lost; snapshot suppressed')
            else:
                try:
                    if not await self._freeze(session):raise DebugAdapterError('freeze_failed','target not proven frozen or stopped')
                    exported, export_warnings = await self._export_snapshot(session)
                    warnings.extend(export_warnings)
                    if exported is None:
                        changed_unknown = True
                        warnings.append('export failed; snapshot suppressed')
                    else:
                        snapshot = exported
                except DebugAdapterError as exc:
                    changed_unknown = True
                    warnings.append('export error: %s' % exc.code)

        if client is not None:
            try:
                await client.close(kill=True)
            except Exception:
                pass
        try:
            await self._teardown(session)
        except Exception:
            pass
        session.state = 'stopped'
        return {
            'session_id': session.id,
            'state': 'stopped',
            'status': status,
            'reason': reason,
            'ok': True,
            'snapshot': snapshot,
            'snapshot_files': len(snapshot) if snapshot else 0,
            'changed_state_unknown': changed_unknown,
            'terminated_observed': session.terminated_observed,
            'exit_code': session.exit_code,
            'warnings': warnings[:20],
        }

    async def cleanup_names(self, session_id: str) -> Dict[str, Any]:
        """Remove ONLY exact UUID/label-owned orphan resources (root restart)."""
        uid = validate_session_uuid(session_id)
        if uid in self._sessions and self._sessions[uid].stop_result is None:
            return {'session_id': uid, 'ok': False, 'error': 'session_active',
                    'reason': 'use stop() for active sessions'}
        names = session_names(uid)
        removed: Dict[str, bool] = {}
        for key in ('container', 'keeper', 'importer', 'exporter'):
            try:
                rc, _, _ = await self._run_captured(
                    [self.binary, 'rm', '-f', names[key]], timeout=15)
                removed[key] = rc == 0
            except DebugAdapterError:
                removed[key] = False
        label_filter = 'label=%s=%s' % (SESSION_LABEL_KEY, uid)
        try:
            rc, out, _ = await self._run_captured(
                [self.binary, 'ps', '-aq', '--filter', label_filter], timeout=15)
            if rc == 0:
                for line in out.decode('utf-8', 'replace').split():
                    try:
                        await self._run_captured([self.binary, 'rm', '-f', line], timeout=15)
                    except DebugAdapterError:
                        pass
        except DebugAdapterError:
            pass
        try:
            rc, _, _ = await self._run_captured(
                [self.binary, 'volume', 'rm', '-f', names['volume']], timeout=15)
            removed['volume'] = rc == 0
        except DebugAdapterError:
            removed['volume'] = False
        try:
            rc, out, _ = await self._run_captured(
                [self.binary, 'volume', 'ls', '-q', '--filter', label_filter], timeout=15)
            if rc == 0:
                for line in out.decode('utf-8', 'replace').split():
                    try:
                        await self._run_captured([self.binary, 'volume', 'rm', '-f', line],
                                                 timeout=15)
                    except DebugAdapterError:
                        pass
        except DebugAdapterError:
            pass
        return {'session_id': uid, 'ok': True, 'removed': removed}
