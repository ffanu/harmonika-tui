"""Tests for the OpenCode-owned Docker coding sandbox (mock + real).

Real-Docker tests skip clearly when docker or the image is unavailable; mock
tests prove argv containment (no host shell, no host bind mounts), per-job
stop ownership, output/snapshot limits, skill dispatch and honest
unavailability. Root-review regressions are covered explicitly: the keeper
container holds the tmpfs volume across import/exec/export, all containers
use --log-driver=none + nofile/core ulimits + harmonika.job labels, the tmpfs
volume carries nr_inodes=4096, GNU timeout -k 2 runs as PID1 watchdog,
stdout/stderr are pumped concurrently (no sequential-pump deadlock), skill
timeouts span 1..120, test.run obeys its timeout, and git status/diff/commit
use safe config (hooksPath=/dev/null, fsmonitor=false, gpgsign off),
--no-ext-diff/--no-textconv, git init, default identity and --all staging.
Run: python3 tests/test_coding_sandbox.py  (or unittest discover).
"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CODING_DIR = ROOT / 'backend' / 'coding_runtime'
sys.path.insert(0, str(CODING_DIR))

import sandbox as sb  # noqa: E402
import volume_io as vio  # noqa: E402

VOLUME_IO_PATH = str(CODING_DIR / 'volume_io.py')
BINARY = '/usr/bin/docker'
IMAGE = 'harmonika-coding:20261024'


def b64(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    return base64.b64encode(data).decode('ascii')


def fresh_job_id():
    return str(uuid.uuid4())


def call_named(captured_calls, name):
    return next(a for a, _ in captured_calls
                if '--name' in a and a[a.index('--name') + 1] == name)


def after_image(argv):
    return argv[argv.index(IMAGE) + 1:]


class FakeDocker:
    def __init__(self, exec_result=(0, b'ok\n', b''), exec_overflow=(), exec_timed=False,
                 export_payload=None, io_workspace=None, hold_until_stop=False,
                 hold_event=None, exec_delay=0.0, keeper_running=True,
                 keeper_alive_after=None, keeper_start_fails=False):
        self.exec_result = exec_result
        self.exec_overflow = set(exec_overflow)
        self.exec_timed = exec_timed
        self.export_payload = export_payload
        self.io_workspace = io_workspace
        self.hold_until_stop = hold_until_stop
        self.hold_event = hold_event
        self.exec_delay = exec_delay
        self.keeper_running = keeper_running
        self.keeper_alive_after = keeper_alive_after
        self.keeper_start_fails = keeper_start_fails
        self.captured_calls = []
        self.streamed_calls = []
        self.budgets = []
        self.inspect_calls = []
        self.keeper_start_calls = []
        self.kill_names = []
        self.active = 0
        self.max_active_seen = 0

    async def captured(self, argv, *, input_bytes=None, timeout=None):
        self.captured_calls.append((list(argv), input_bytes is not None))
        if any('/opt/harmonika/volume_io.py' in part for part in argv):
            return self._volume_io(argv, input_bytes)
        if argv[1] == 'inspect':
            self.inspect_calls.append(argv[-1])
            alive = self.keeper_running
            if self.keeper_alive_after is not None and len(self.inspect_calls) > self.keeper_alive_after:
                alive = False
            return (0, b'true\n' if alive else b'false\n', b'')
        if argv[1] == 'run' and '-d' in argv:
            self.keeper_start_calls.append(list(argv))
            if self.keeper_start_fails:
                return (1, b'', b'keeper failed to start')
            return (0, b'cid123\n', b'')
        if argv[1] == 'kill':
            self.kill_names.append(argv[2])
        return (0, b'', b'')

    def _volume_io(self, argv, input_bytes):
        if self.io_workspace is None:
            if '--export' in argv:
                return (0, self.export_payload or b'', b'')
            return (0, b'{"ok": true}', b'')
        argv2 = list(argv)
        tail_at = argv2.index('/opt/harmonika/volume_io.py')
        cmd = [sys.executable, VOLUME_IO_PATH, '--workspace', self.io_workspace] + argv2[tail_at + 1:]
        proc = subprocess.run(cmd, input=input_bytes or b'', capture_output=True)
        return (proc.returncode, proc.stdout, proc.stderr)

    async def streamed(self, argv, *, on_output, job, budget):
        self.streamed_calls.append(list(argv))
        self.budgets.append(budget)
        job.running.add(job.container)
        self.active += 1
        self.max_active_seen = max(self.max_active_seen, self.active)
        try:
            if self.exec_delay:
                await asyncio.sleep(self.exec_delay)
            if self.hold_event is not None:
                await self.hold_event.wait()
            if self.hold_until_stop:
                while not job.stop_requested:
                    await asyncio.sleep(0.005)
                return (None, b'', b'', set(), False)
            if on_output is not None and self.exec_result[1]:
                await on_output({'job_id': job.job_id, 'stream': 'stdout',
                                 'data': self.exec_result[1].decode('utf-8', 'replace')})
            return (self.exec_result[0], self.exec_result[1], self.exec_result[2],
                    self.exec_overflow, self.exec_timed)
        finally:
            self.active -= 1
            job.running.discard(job.container)

    def kill_calls(self):
        return [argv for argv, _ in self.captured_calls if argv[1] == 'kill']


def make_sandbox(fake, **kwargs):
    box = sb.DockerSandbox(image=IMAGE, binary=BINARY, **kwargs)
    box._run_captured = fake.captured
    box._run_streamed = fake.streamed
    return box


class PlanDispatchTests(unittest.TestCase):
    def test_shell_plan_uses_sh_lc(self):
        plan = sb.build_plan('exec.shell', {'command': 'echo hi', 'timeout_seconds': 45})
        self.assertEqual(plan.steps, [['/bin/sh', '-lc', 'echo hi']])
        self.assertEqual(plan.step_timeout, 45.0)

    def test_timeout_range_is_1_to_120(self):
        self.assertEqual(sb.SKILL_TIMEOUT_MIN, 1)
        self.assertEqual(sb.SKILL_TIMEOUT_MAX, 120)
        self.assertEqual(sb.build_plan('exec.shell', {'command': 'x', 'timeout_seconds': 1}).step_timeout, 1.0)
        self.assertEqual(sb.build_plan('exec.shell', {'command': 'x', 'timeout_seconds': 10}).step_timeout, 10.0)
        self.assertEqual(sb.build_plan('exec.shell', {'command': 'x', 'timeout_seconds': 120}).step_timeout, 120.0)
        for bad in (0, 0.5, 121, '30', True, None):
            with self.assertRaises(sb.SandboxError, msg=repr(bad)):
                sb.build_plan('exec.shell', {'command': 'x', 'timeout_seconds': bad})

    def test_watchdog_wrap_format(self):
        self.assertEqual(sb.watchdog_wrap(30, ['/bin/sh', '-lc', 'x']),
                         ['timeout', '-k', '2', '30', '/bin/sh', '-lc', 'x'])
        self.assertEqual(sb.watchdog_wrap(7, ['python3', 'a.py']),
                         ['timeout', '-k', '2', '7', 'python3', 'a.py'])
        self.assertEqual(sb.watchdog_wrap(1.5, ['x']), ['timeout', '-k', '2', '1.5', 'x'])
        with self.assertRaises(sb.SandboxError):
            sb.watchdog_wrap(30, [])

    def test_python_code_and_path(self):
        plan = sb.build_plan('exec.python', {'code': 'print(1)', 'args': ['--x']})
        self.assertEqual(plan.steps, [['python3', '-c', 'print(1)', '--x']])
        plan = sb.build_plan('exec.python', {'path': 'scripts/a.py'})
        self.assertEqual(plan.steps, [['python3', 'scripts/a.py']])

    def test_node_analogous(self):
        plan = sb.build_plan('exec.node', {'code': 'console.log(1)'})
        self.assertEqual(plan.steps, [['node', '-e', 'console.log(1)']])
        plan = sb.build_plan('exec.node', {'path': 'a.js'})
        self.assertEqual(plan.steps, [['node', 'a.js']])

    def test_test_runners_fixed_commands(self):
        self.assertEqual(sb.build_plan('test.run', {'runner': 'python_unittest'}).steps,
                         [['python3', '-m', 'unittest', 'discover', '-s', '.', '-t', '/workspace']])
        self.assertEqual(sb.build_plan('test.run', {'runner': 'node_test', 'path': 'tests'}).steps,
                         [['node', '--test', 'tests']])
        self.assertEqual(sb.build_plan('test.run', {'runner': 'npm_test', 'filter': 'api'}).steps,
                         [['npm', 'test', '--', 'api']])

    def test_test_run_timeout_obeyed(self):
        self.assertEqual(sb.build_plan('test.run', {'runner': 'python_unittest'}).step_timeout, 120.0)
        self.assertEqual(sb.build_plan('test.run', {'runner': 'python_unittest',
                                                    'timeout_seconds': 5}).step_timeout, 5.0)
        with self.assertRaises(sb.SandboxError):
            sb.build_plan('test.run', {'runner': 'npm_test', 'timeout_seconds': 121})
        with self.assertRaises(sb.SandboxError):
            sb.build_plan('test.run', {'runner': 'npm_test', 'timeout_seconds': 0})

    def test_git_safe_config_tuple(self):
        self.assertEqual(sb.GIT_SAFE_CONFIG,
                         ('-c', 'core.hooksPath=/dev/null',
                          '-c', 'core.fsmonitor=false',
                          '-c', 'commit.gpgsign=false'))

    def test_git_status_and_diff_use_safe_flags(self):
        safe = list(sb.GIT_SAFE_CONFIG)
        self.assertEqual(sb.build_plan('git.status', {}).steps,
                         [['git'] + safe + ['status', '--porcelain=v1', '-b']])
        self.assertEqual(sb.build_plan('git.diff', {}).steps,
                         [['git'] + safe + ['diff', '--no-color', '--no-ext-diff', '--no-textconv']])
        self.assertEqual(sb.build_plan('git.diff', {'staged': True}).steps,
                         [['git'] + safe + ['diff', '--no-color', '--no-ext-diff', '--no-textconv', '--cached']])

    def test_git_commit_inits_stages_all_default_identity(self):
        safe = list(sb.GIT_SAFE_CONFIG)
        plan = sb.build_plan('git.commit', {'message': 'm'})
        self.assertEqual(plan.steps, [
            ['git'] + safe + ['init', '-q'],
            ['git'] + safe + ['add', '--all'],
            ['git'] + safe + ['-c', 'user.name=%s' % sb.DEFAULT_GIT_NAME,
                              '-c', 'user.email=%s' % sb.DEFAULT_GIT_EMAIL,
                              'commit', '-m', 'm'],
        ])

    def test_git_commit_explicit_identity_and_paths(self):
        safe = list(sb.GIT_SAFE_CONFIG)
        plan = sb.build_plan('git.commit', {'message': 'm', 'paths': ['a.txt'],
                                            'author_name': 'A', 'author_email': 'a@b.c'})
        self.assertEqual(plan.steps[0], ['git'] + safe + ['init', '-q'])
        self.assertEqual(plan.steps[1], ['git'] + safe + ['add', '--', 'a.txt'])
        self.assertEqual(plan.steps[2], ['git'] + safe + ['-c', 'user.name=A', '-c', 'user.email=a@b.c',
                                                          'commit', '-m', 'm'])

    def test_rejections(self):
        for skill, args in [
            ('exec.shell', {'command': 'x', 'timeout_seconds': 0}),
            ('exec.shell', {'command': 'x', 'timeout_seconds': 121}),
            ('exec.shell', {}),
            ('exec.python', {'code': 'x', 'path': 'y'}),
            ('exec.node', {}),
            ('test.run', {'runner': 'make'}),
            ('git.commit', {'message': 'x', 'paths': ['../etc']}),
            ('git.commit', {'message': 'x', 'author_name': '-evil'}),
            ('unknown.skill', {}),
        ]:
            with self.assertRaises(sb.SandboxError, msg='%s %r' % (skill, args)):
                sb.build_plan(skill, args)

    def test_path_validator(self):
        for bad in ['/abs', '../up', 'a/../b', 'a//b', './x', 'a\x00b', 'a' * 241, 'a\\b', '.']:
            with self.assertRaises(sb.SandboxError, msg=repr(bad)):
                sb.validate_rel_path(bad)
        self.assertEqual(sb.validate_rel_path('.', allow_root_dot=True), '.')
        self.assertEqual(sb.validate_rel_path('d/e.txt'), 'd/e.txt')

    def test_snapshot_bounds(self):
        with self.assertRaises(sb.SandboxError):
            sb.validate_snapshot_data({('f%03d.txt' % i): b'x' for i in range(257)})
        with self.assertRaises(sb.SandboxError):
            sb.validate_snapshot_data({'big.bin': b'x' * (512 * 1024 + 1)})
        with self.assertRaises(sb.SandboxError):
            sb.validate_snapshot_data({'../escape': b'x'})
        ok = sb.validate_snapshot_data({'a.txt': b'x'})
        self.assertEqual(ok, {'a.txt': b'x'})


class ExecArgvContainmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_exec_argv_fully_contained(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        jid = fresh_job_id()
        result = await box.run({'a.txt': b64('hi')}, 'exec.shell',
                               {'command': 'echo hi', 'timeout_seconds': 30}, jid)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 0)
        argv = fake.streamed_calls[0]
        self.assertEqual(argv[0], BINARY)
        for flag in sb.EXEC_FLAGS:
            self.assertIn(flag, argv, flag)
        self.assertNotIn('--privileged', argv)
        self.assertNotIn('-p', argv)
        self.assertFalse(any('docker.sock' in part for part in argv))
        name_idx = argv.index('--name')
        self.assertEqual(argv[name_idx + 1], 'hms-coding-' + jid)
        vol_idx = argv.index('-v')
        vol_value = argv[vol_idx + 1]
        self.assertEqual(vol_value, 'hms-coding-vol-%s:/workspace:nocopy' % jid)
        host_part = vol_value.split(':')[0]
        self.assertNotIn('/', host_part)
        self.assertEqual(after_image(argv),
                         ['timeout', '-k', '2', '30', '/bin/sh', '-lc', 'echo hi'])
        self.assertFalse(any(part == '/bin/sh' for part in argv[:argv.index(IMAGE)]))
        # log disk / ulimit / label hardening on the exec container
        self.assertIn('--log-driver=none', argv)
        ulimit_values = {argv[i + 1] for i, p in enumerate(argv) if p == '--ulimit'}
        self.assertEqual(ulimit_values, {'nofile=128', 'core=0'})
        self.assertEqual(argv[argv.index('--label') + 1], 'harmonika.job=%s' % jid)
        # tmpfs volume opts carry nr_inodes
        create = fake.captured_calls[0][0]
        self.assertEqual(create[1], 'volume')
        self.assertIn('o=' + sb.VOLUME_TMPFS, create)
        self.assertIn('nr_inodes=4096', sb.VOLUME_TMPFS)
        self.assertEqual('hms-coding-vol-' + jid, create[create.index('--name') + 1])
        # import helper: same restrictions, watchdog-wrapped trusted command
        import_argv = call_named(fake.captured_calls, 'hms-coding-i-' + jid)
        self.assertIn('--interactive', import_argv)
        self.assertEqual(after_image(import_argv),
                         ['timeout', '-k', '2', '60', 'python3',
                          '/opt/harmonika/volume_io.py', '--import'])
        for flag in sb.EXEC_FLAGS:
            self.assertIn(flag, import_argv, flag)
        self.assertIn('--log-driver=none', import_argv)
        # export helper likewise
        export_argv = call_named(fake.captured_calls, 'hms-coding-o-' + jid)
        self.assertEqual(after_image(export_argv),
                         ['timeout', '-k', '2', '60', 'python3',
                          '/opt/harmonika/volume_io.py', '--export'])
        for flag in sb.EXEC_FLAGS:
            self.assertIn(flag, export_argv, flag)
        self.assertIn('--log-driver=none', export_argv)
        # keeper holds the volume across the whole pipeline
        keeper_name = 'hms-coding-k-' + jid
        keeper_argv = call_named(fake.captured_calls, keeper_name)
        self.assertIn('-d', keeper_argv)
        self.assertIn('--log-driver=none', keeper_argv)
        self.assertEqual(keeper_argv[keeper_argv.index('--label') + 1], 'harmonika.job=%s' % jid)
        for flag in sb.EXEC_FLAGS:
            self.assertIn(flag, keeper_argv, flag)
        kv_idx = keeper_argv.index('-v')
        self.assertEqual(keeper_argv[kv_idx + 1], 'hms-coding-vol-%s:/workspace:nocopy' % jid)
        self.assertEqual(after_image(keeper_argv), ['sleep', '180'])
        # keeper liveness is verified before exec and before export
        inspected = [a[-1] for a, _ in fake.captured_calls if a[1] == 'inspect']
        self.assertEqual(inspected, [keeper_name, keeper_name])
        # cleanup removes every own container then the volume
        rm_names = [a[3] for a, _ in fake.captured_calls if a[1] == 'rm' and a[2] == '-f']
        self.assertIn('hms-coding-' + jid, rm_names)
        self.assertIn('hms-coding-i-' + jid, rm_names)
        self.assertIn('hms-coding-o-' + jid, rm_names)
        self.assertIn(keeper_name, rm_names)
        vol_rm = [a for a, _ in fake.captured_calls if a[1] == 'volume' and a[2] == 'rm']
        self.assertTrue(vol_rm)

    async def test_git_commit_runs_three_watchdog_steps(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        result = await box.run({}, 'git.commit', {'message': 'm'}, fresh_job_id())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(fake.streamed_calls), 3)
        for argv in fake.streamed_calls:
            self.assertEqual(after_image(argv)[:4], ['timeout', '-k', '2', '60'])
        self.assertIn('init', fake.streamed_calls[0])
        self.assertIn('--all', fake.streamed_calls[1])
        self.assertIn('commit', fake.streamed_calls[2])

    async def test_no_docker_call_on_invalid_snapshot(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        bad = {('f%03d.txt' % i): b64('x') for i in range(257)}
        result = await box.run(bad, 'exec.shell', {'command': 'x'}, fresh_job_id())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'invalid_request')
        self.assertEqual(fake.captured_calls, [])
        self.assertEqual(fake.streamed_calls, [])

    async def test_unknown_skill_fails_without_docker(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        result = await box.run({}, 'rm.rf', {'path': '.'}, fresh_job_id())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(fake.captured_calls, [])

    async def test_output_callback_receives_chunks(self):
        chunks = []

        async def on_output(chunk):
            chunks.append(chunk)

        fake = FakeDocker(exec_result=(0, b'hello\n', b''))
        box = make_sandbox(fake)
        result = await box.run({}, 'exec.shell', {'command': 'echo hello'}, fresh_job_id(),
                               on_output=on_output)
        self.assertEqual(result['stdout'], 'hello\n')
        self.assertEqual([c['stream'] for c in chunks], ['stdout'])
        self.assertEqual(chunks[0]['data'], 'hello\n')

    async def test_step_budget_and_watchdog_respect_configured_timeout(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        await box.run({}, 'exec.shell', {'command': 'x', 'timeout_seconds': 7}, fresh_job_id())
        self.assertLessEqual(fake.budgets[0], 7.0)
        self.assertEqual(after_image(fake.streamed_calls[0])[:4], ['timeout', '-k', '2', '7'])
        result = await box.run({}, 'test.run', {'runner': 'python_unittest',
                                                'timeout_seconds': 5}, fresh_job_id())
        self.assertEqual(result['status'], 'completed')
        self.assertLessEqual(fake.budgets[1], 5.0)
        self.assertEqual(after_image(fake.streamed_calls[1])[:4], ['timeout', '-k', '2', '5'])

    async def test_max_active_serializes(self):
        fake = FakeDocker(exec_delay=0.02)
        box = make_sandbox(fake, max_active=1)

        async def one():
            return await box.run({}, 'exec.shell', {'command': 'x'}, fresh_job_id())

        await asyncio.gather(one(), one())
        self.assertEqual(fake.max_active_seen, 1)

    async def test_background_returns_job_then_completes(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        jid = fresh_job_id()
        started = await box.run({}, 'exec.background', {'command': 'long job'}, jid)
        self.assertEqual(started['status'], 'started')
        result = await box.job_result(jid)
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(await box.stop(jid))


class StopOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_unknown_is_false_and_touches_nothing(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        self.assertFalse(await box.stop(fresh_job_id()))
        self.assertFalse(await box.stop('not-a-uuid'))
        self.assertEqual(fake.captured_calls, [])

    async def test_stop_hits_only_own_tracked_names_and_not_keeper(self):
        fake = FakeDocker(hold_until_stop=True)
        box = make_sandbox(fake)
        jid = fresh_job_id()
        run_task = asyncio.ensure_future(
            box.run({'a.txt': b64('x')}, 'exec.shell', {'command': 'sleep 100'}, jid))
        for _ in range(500):
            job = box._jobs.get(jid)
            if job is not None and job.container in job.running:
                break
            await asyncio.sleep(0.005)
        keeper_name = 'hms-coding-k-' + jid
        self.assertIn(keeper_name, [a[-1] for a, _ in fake.captured_calls if a[1] == 'inspect'])
        self.assertTrue(await box.stop(jid))
        result = await run_task
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['changed_state_unknown'], True)
        # only the exec container is killed: stop must NOT kill the keeper
        killed = fake.kill_calls()
        self.assertEqual(killed, [[BINARY, 'kill', 'hms-coding-' + jid]])
        self.assertNotIn(keeper_name, fake.kill_names)
        self.assertFalse(await box.stop(jid))

    async def test_duplicate_active_job_rejected(self):
        fake = FakeDocker(hold_until_stop=True)
        box = make_sandbox(fake)
        jid = fresh_job_id()
        first = asyncio.ensure_future(box.run({}, 'exec.shell', {'command': 'x'}, jid))
        for _ in range(500):
            job = box._jobs.get(jid)
            if job is not None and job.container in job.running:
                break
            await asyncio.sleep(0.005)
        dup = await box.run({}, 'exec.shell', {'command': 'x'}, jid)
        self.assertEqual(dup['status'], 'failed')
        self.assertEqual(dup['error'], 'duplicate_job_id')
        self.assertTrue(await box.stop(jid))
        result = await first
        self.assertEqual(result['status'], 'stopped')


class KeeperLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeper_start_failure_fails_job_without_exec(self):
        fake = FakeDocker(keeper_start_fails=True)
        box = make_sandbox(fake)
        result = await box.run({'a.txt': b64('x')}, 'exec.shell', {'command': 'x'}, fresh_job_id())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'keeper_failed')
        self.assertEqual(fake.streamed_calls, [])
        self.assertFalse(any('--import' in a for a, _ in fake.captured_calls))
        # volume and any partial containers are still cleaned up
        self.assertTrue(any(a[1] == 'volume' and a[2] == 'rm' for a, _ in fake.captured_calls))

    async def test_keeper_dead_before_exec_fails_unknown(self):
        fake = FakeDocker(keeper_running=False)
        box = make_sandbox(fake)
        result = await box.run({'a.txt': b64('x')}, 'exec.shell', {'command': 'x'}, fresh_job_id())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'keeper_lost')
        self.assertTrue(result['changed_state_unknown'])
        self.assertEqual(fake.streamed_calls, [])
        self.assertFalse(any('--export' in a for a, _ in fake.captured_calls))

    async def test_keeper_dead_before_export_skips_export(self):
        fake = FakeDocker(keeper_alive_after=1)
        box = make_sandbox(fake)
        result = await box.run({'a.txt': b64('x')}, 'exec.shell', {'command': 'x'}, fresh_job_id())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['reason'], 'keeper_lost')
        self.assertTrue(result['changed_state_unknown'])
        self.assertNotIn('snapshot', result)
        self.assertFalse(any('--export' in a for a, _ in fake.captured_calls))


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_limits_document_new_guarantees(self):
        box = sb.DockerSandbox(image=IMAGE, binary=BINARY)
        lim = box.limits()
        self.assertEqual(lim['skill_timeout_range_seconds'], [1, 120])
        self.assertIn('nr_inodes=4096', lim['volume']['opts'])
        self.assertEqual(lim['container']['log_driver'], 'none')
        self.assertEqual(lim['container']['ulimits'], {'nofile': 128, 'core': 0})
        self.assertEqual(lim['container']['watchdog_pid1'], 'timeout -k 2 <seconds>')
        self.assertEqual(lim['container_label'], 'harmonika.job')
        self.assertEqual(lim['keeper']['pid1'], 'sleep 180')
        self.assertEqual(lim['keeper']['across'], ['import', 'exec', 'export'])

    async def test_overflow_fails_bounded_and_kills_own_container(self):
        fake = FakeDocker(exec_result=(137, b'x' * (sb.OUTPUT_LIMIT + 100), b''),
                          exec_overflow=('stdout',))
        box = make_sandbox(fake)
        jid = fresh_job_id()
        result = await box.run({}, 'exec.shell', {'command': 'spam'}, jid)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'output_overflow')
        self.assertLessEqual(len(result['stdout'].encode()), sb.OUTPUT_LIMIT)
        self.assertEqual(fake.kill_calls(), [[BINARY, 'kill', 'hms-coding-' + jid]])
        self.assertNotIn('hms-coding-k-' + jid, fake.kill_names)

    async def test_real_stream_bounded(self):
        box = sb.DockerSandbox(image=IMAGE, binary=BINARY)
        recorder = FakeDocker()
        box._run_captured = recorder.captured
        jid = fresh_job_id()
        job = box._register(jid)
        chunks = []

        async def on_output(chunk):
            chunks.append(chunk)

        script = "import sys; sys.stdout.write('x' * %d)" % (sb.OUTPUT_LIMIT * 2)
        argv = [sys.executable, '-c', script]
        rc, out, err, overflow, timed = await box._run_streamed(
            argv, on_output=on_output, job=job, budget=30.0)
        self.assertFalse(timed)
        self.assertLessEqual(len(out), sb.OUTPUT_LIMIT)
        self.assertLessEqual(sum(len(c['data'].encode()) for c in chunks), sb.OUTPUT_LIMIT)
        self.assertIn(job.container, recorder.kill_names)
        self.assertEqual(recorder.kill_names, ['hms-coding-' + jid])

    async def test_stdout_stderr_pumped_concurrently_no_deadlock(self):
        box = sb.DockerSandbox(image=IMAGE, binary=BINARY)
        recorder = FakeDocker()
        box._run_captured = recorder.captured
        job = box._register(fresh_job_id())
        # A child that keeps stdout open while flooding stderr would deadlock a
        # sequential stdout-then-stderr pump (64KiB pipe fills, child blocks,
        # stdout never reaches EOF). Concurrent pumping must finish cleanly.
        script = (
            "import sys, time\n"
            "sys.stdout.write('stdout-start\\n'); sys.stdout.flush()\n"
            "for _ in range(40):\n"
            "    sys.stderr.write('e' * 10000 + '\\n'); sys.stderr.flush()\n"
            "    time.sleep(0.005)\n"
            "sys.stdout.write('stdout-end\\n'); sys.stdout.flush()\n"
        )
        argv = [sys.executable, '-c', script]
        chunks = []

        async def on_output(chunk):
            chunks.append(chunk)

        rc, out, err, overflow, timed = await box._run_streamed(
            argv, on_output=on_output, job=job, budget=30.0)
        self.assertFalse(timed)
        self.assertEqual(rc, 0)
        self.assertFalse(overflow)
        out_text = out.decode()
        self.assertIn('stdout-start', out_text)
        self.assertIn('stdout-end', out_text)
        self.assertGreaterEqual(len(err), 400000)
        self.assertEqual({c['stream'] for c in chunks}, {'stdout', 'stderr'})
        self.assertEqual(recorder.kill_names, [])

    async def test_timeout_kills_own_container(self):
        fake = FakeDocker(exec_timed=True)
        box = make_sandbox(fake)
        jid = fresh_job_id()
        result = await box.run({}, 'exec.shell', {'command': 'sleep forever'}, jid)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['exit_code'], None)
        self.assertEqual(fake.kill_calls(), [[BINARY, 'kill', 'hms-coding-' + jid]])

    async def test_failed_export_marks_changed_state_unknown(self):
        fake = FakeDocker(exec_result=(1, b'', b'boom'))

        async def captured(argv, *, input_bytes=None, timeout=None):
            fake.captured_calls.append((list(argv), input_bytes is not None))
            if argv[1] == 'inspect':
                return (0, b'true\n', b'')
            if '--export' in argv:
                return (1, b'', b'export boom')
            if argv[1] == 'kill':
                fake.kill_names.append(argv[2])
            return (0, b'', b'')

        fake.captured = captured
        box = make_sandbox(fake)
        result = await box.run({'a.txt': b64('x')}, 'exec.shell', {'command': 'false'}, fresh_job_id())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['exit_code'], 1)
        self.assertTrue(result['changed_state_unknown'])
        self.assertNotIn('snapshot', result)

    async def test_result_carries_limits(self):
        fake = FakeDocker()
        box = make_sandbox(fake)
        result = await box.run({}, 'git.status', {}, fresh_job_id())
        self.assertEqual(result['limits'], box.limits())
        self.assertEqual(result['limits']['container']['network'], 'none')
        self.assertEqual(result['limits']['container']['user'], '10000:10000')


class VolumeIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hms-vio-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_roundtrip(self):
        root = os.path.join(self.tmp, 'ws')
        os.makedirs(root)
        snap = {'a.txt': b'alpha', 'd/b.txt': b'beta', '.git/HEAD': b'ref: refs/heads/main\n'}
        vio.do_import(root, {'snapshot': {k: b64(v) for k, v in snap.items()}})
        exported = vio.do_export(root)
        self.assertEqual(exported['warnings'], [])
        decoded = {k: base64.b64decode(v) for k, v in exported['snapshot'].items()}
        self.assertEqual(decoded, snap)

    def test_export_skips_symlink_special_hardlink_credential(self):
        root = os.path.join(self.tmp, 'ws')
        os.makedirs(os.path.join(root, 'd'))
        with open(os.path.join(root, 'real.txt'), 'w') as fh:
            fh.write('data')
        os.symlink('real.txt', os.path.join(root, 'link.txt'))
        with open(os.path.join(root, '.env'), 'w') as fh:
            fh.write('SECRET=1')
        try:
            os.mkfifo(os.path.join(root, 'd/pipe'))
            has_fifo = True
        except (AttributeError, OSError):
            has_fifo = False
        os.link(os.path.join(root, 'real.txt'), os.path.join(root, 'hard.txt'))
        exported = vio.do_export(root)
        self.assertEqual(set(exported['snapshot']), set())
        warnings = ' '.join(exported['warnings'])
        self.assertIn('link.txt', warnings)
        self.assertIn('.env', warnings)
        self.assertIn('hard.txt', warnings)
        self.assertIn('real.txt', warnings)
        if has_fifo:
            self.assertIn('pipe', warnings)

    def test_import_rejects_traversal_and_oversize(self):
        root = os.path.join(self.tmp, 'ws')
        os.makedirs(root)
        with self.assertRaises(vio.VolumeIOError):
            vio.do_import(root, {'snapshot': {'../escape': b64('x')}})
        with self.assertRaises(vio.VolumeIOError):
            vio.do_import(root, {'snapshot': {'big': b64(b'x' * (512 * 1024 + 1))}})
        with self.assertRaises(vio.VolumeIOError):
            vio.do_import(root, {'snapshot': {('f%03d' % i): b64('x') for i in range(257)}})
        self.assertEqual(os.listdir(root), [])

    def test_import_replaces_previous_tree(self):
        root = os.path.join(self.tmp, 'ws')
        os.makedirs(root)
        vio.do_import(root, {'snapshot': {'old.txt': b64('old'), 'd/x': b64('x')}})
        vio.do_import(root, {'snapshot': {'new.txt': b64('new')}})
        self.assertEqual(sorted(os.listdir(root)), ['new.txt'])

    def test_cli_import_export_exit_codes(self):
        root = os.path.join(self.tmp, 'ws')
        os.makedirs(root)
        proc = subprocess.run(
            [sys.executable, VOLUME_IO_PATH, '--import', '--workspace', root],
            input=b'not json', capture_output=True)
        self.assertEqual(proc.returncode, 2)
        proc = subprocess.run(
            [sys.executable, VOLUME_IO_PATH, '--import', '--workspace', root],
            input=json.dumps({'snapshot': {'ok.txt': b64('yes')}}).encode(), capture_output=True)
        self.assertEqual(proc.returncode, 0)
        proc = subprocess.run(
            [sys.executable, VOLUME_IO_PATH, '--export', '--workspace', root],
            capture_output=True)
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout.decode())
        self.assertEqual(base64.b64decode(payload['snapshot']['ok.txt']), b'yes')
        self.assertEqual(payload['warnings'], [])


class RoundtripIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_pipeline_with_real_volume_io(self):
        workspace = tempfile.mkdtemp(prefix='hms-rt-')
        self.addCleanup(shutil.rmtree, workspace, True)
        fake = FakeDocker(io_workspace=workspace)

        async def streamed(argv, *, on_output, job, budget):
            fake.streamed_calls.append(list(argv))
            with open(os.path.join(workspace, 'made-by-job.txt'), 'w') as fh:
                fh.write('created inside sandbox')
            return (0, b'step done\n', b'', set(), False)

        fake.streamed = streamed
        box = make_sandbox(fake)
        jid = fresh_job_id()
        result = await box.run({'seed.txt': b64('seed')}, 'exec.shell',
                               {'command': 'echo seeded'}, jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertNotIn('changed_state_unknown', result)
        snapshot = result['snapshot']
        self.assertEqual(base64.b64decode(snapshot['seed.txt']), b'seed')
        self.assertEqual(base64.b64decode(snapshot['made-by-job.txt']), b'created inside sandbox')
        self.assertEqual(result['stdout'], 'step done\n')

    async def test_invalid_export_json_is_unknown(self):
        fake = FakeDocker(export_payload=b'not json at all')
        box = make_sandbox(fake)
        result = await box.run({'a.txt': b64('x')}, 'git.status', {}, fresh_job_id())
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['changed_state_unknown'])
        self.assertNotIn('snapshot', result)


class UnavailableTests(unittest.IsolatedAsyncioTestCase):
    async def test_capabilities_honest_when_binary_missing(self):
        box = sb.DockerSandbox(image=IMAGE, binary='/nonexistent/hms-docker')
        caps = await box.capabilities()
        self.assertFalse(caps['docker_available'])
        self.assertFalse(caps['image_available'])
        self.assertFalse(caps['ready'])
        self.assertTrue(caps['error'])

    async def test_run_fails_cleanly_when_binary_missing(self):
        box = sb.DockerSandbox(image=IMAGE, binary='/nonexistent/hms-docker')
        result = await box.run({'a.txt': b64('x')}, 'exec.shell', {'command': 'x'}, fresh_job_id())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'volume_create_failed')


class RealDockerTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_capabilities_and_exec_when_available(self):
        box = sb.DockerSandbox(image=IMAGE, binary=BINARY)
        caps = await box.capabilities()
        if not caps.get('ready'):
            raise unittest.SkipTest(
                'Docker or image %s unavailable (docker_available=%s, image_available=%s); '
                'mock tests above cover isolation argv, ownership and limits.'
                % (IMAGE, caps.get('docker_available'), caps.get('image_available')))
        jid = fresh_job_id()
        result = await box.run({'hello.txt': b64('hi')}, 'exec.shell',
                               {'command': 'cat hello.txt && id -u'}, jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('10000', result['stdout'])
        self.assertEqual(result['snapshot'].get('hello.txt'), b64('hi'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
