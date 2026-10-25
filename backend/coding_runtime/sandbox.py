"""Async Docker sandbox for Harmonika coding MVP jobs (OpenCode-owned).

Per job this sandbox uses a LOCAL tmpfs Docker volume (never a read-write
host bind mount), one long-lived trusted "keeper" container that stays
attached to the volume across the whole import -> exec -> export pipeline
(a local-driver tmpfs volume is unmounted, and its data lost, when the last
attached container exits), trusted volume_io import/export helper containers,
and one isolated exec container per command step.

Host-side hard guarantees:
- the Docker CLI is the only host interface and is always invoked as an argv
  list via asyncio.create_subprocess_exec (no host shell, no host paths);
- every container runs with --log-driver=none (no host log disk growth) and
  nofile=128 / core=0 ulimits, and carries the harmonika.job=<job uuid>
  label for ownership audit;
- container and volume names are strictly derived from the job UUID with the
  hms-coding- prefix and tracked in memory; stop() touches ONLY the exact
  transient names in this adapter's active record (never the keeper, no host
  PID kills, no signals to unrelated processes);
- exec containers are read-only-rootfs, network=none, cap-drop=ALL,
  no-new-privileges, pids/memory/cpu limited, uid/gid 10000, private tmpfs;
- every exec command runs as `timeout -k 2 <seconds> <command>` so GNU
  timeout is PID1 inside the container: a watchdog independent of the host
  event loop (it still kills the command if the host service restarts, and
  killing the timeout PID1 ends the whole PID namespace);
- stdout and stderr are pumped concurrently (asyncio.gather), so a child
  filling one pipe can never deadlock the other stream; output per stream is
  bounded at 512KiB and overflow stops only this job's container;
- skill timeouts are 1..120 seconds; test.run obeys its timeout both on the
  host budget and through the in-container watchdog;
- git.commit runs `git init` when needed, stages `--all` by default, and
  applies a default identity plus safe config (core.hooksPath=/dev/null,
  core.fsmonitor=false, commit.gpgsign=false); git.diff always passes
  --no-ext-diff --no-textconv so no external helper can execute;
- the whole job (import + steps + export) runs under one deadline; on
  timeout only this job's container is killed and our own docker client
  subprocesses are reaped;
- exported snapshots are re-validated (<=256 files, <=512KiB/file,
  <=16MiB total, safe relative paths) before being returned, and only if the
  keeper is still alive (a dead keeper means the tmpfs may have unmounted,
  so no exported snapshot can be trusted). If the final state cannot be
  proven, the result carries changed_state_unknown=True and never claims a
  rollback.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = ['DockerSandbox', 'SandboxError', 'watchdog_wrap']

IMAGE_DEFAULT = 'harmonika-coding:20261024'
SANDBOX_USER = '10000:10000'
CONTAINER_PREFIX = 'hms-coding-'
VOLUME_TMPFS = 'size=32m,uid=10000,gid=10000,mode=0700,nr_inodes=4096,nosuid,nodev'
LOG_DRIVER_FLAGS: Tuple[str, ...] = ('--log-driver=none',)
ULIMIT_FLAGS: Tuple[str, ...] = ('--ulimit', 'nofile=128', '--ulimit', 'core=0')
JOB_LABEL_KEY = 'harmonika.job'
EXEC_FLAGS: Tuple[str, ...] = (
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
OUTPUT_LIMIT = 512 * 1024
SNAPSHOT_MAX_BYTES = 16 * 1024 * 1024
SNAPSHOT_MAX_FILES = 256
SNAPSHOT_MAX_FILE_BYTES = 512 * 1024
SKILL_TIMEOUT_MIN = 1
SKILL_TIMEOUT_MAX = 120
SKILL_TIMEOUT_DEFAULT = 30
OVERALL_MARGIN_SECONDS = 120.0
OVERALL_MAX_SECONDS = 300.0
STEP_TIMEOUTS = {'test.run': 120.0, 'git.status': 60.0, 'git.diff': 60.0, 'git.commit': 60.0}
TEST_RUNNERS = ('python_unittest', 'node_test', 'npm_test')
MAX_ACTIVE_DEFAULT = 2
KEEPER_SECONDS = 180
HELPER_TIMEOUT_SECONDS = 60.0
WATCHDOG_KILL_GRACE_SECONDS = 2
DEFAULT_GIT_NAME = 'Harmonika Sandbox'
DEFAULT_GIT_EMAIL = 'sandbox@harmonika.invalid'
GIT_SAFE_CONFIG: Tuple[str, ...] = (
    '-c', 'core.hooksPath=/dev/null',
    '-c', 'core.fsmonitor=false',
    '-c', 'commit.gpgsign=false',
)
_MAX_COMMAND_LEN = 16384
_MAX_CODE_LEN = 262144
_MAX_ARG_LEN = 1024
_MAX_ARGS = 32
_MAX_MESSAGE_LEN = 2000
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
OutputCallback = Callable[[Dict[str, Any]], Any]


class SandboxError(Exception):
    pass


def format_timeout_arg(seconds: float) -> str:
    value = float(seconds)
    if value == int(value):
        return str(int(value))
    return ('%.3f' % value).rstrip('0').rstrip('.')


def watchdog_wrap(seconds: float, cmd: Sequence[str]) -> List[str]:
    if not cmd:
        raise SandboxError('watchdog command must not be empty')
    return ['timeout', '-k', str(WATCHDOG_KILL_GRACE_SECONDS),
            format_timeout_arg(seconds)] + list(cmd)


def validate_rel_path(path: Any, *, allow_root_dot: bool = False, what: str = 'path') -> str:
    if not isinstance(path, str):
        raise SandboxError('%s must be a string' % what)
    if not path or len(path) > 240:
        raise SandboxError('%s length invalid (1..240)' % what)
    if _CONTROL_RE.search(path):
        raise SandboxError('%s contains control characters' % what)
    if path.startswith('/'):
        raise SandboxError('%s must be relative' % what)
    if '\\' in path:
        raise SandboxError('%s must not contain backslash' % what)
    if path == '.':
        if allow_root_dot:
            return path
        raise SandboxError('%s must name a file' % what)
    for part in path.split('/'):
        if part in ('', '.', '..'):
            raise SandboxError('%s traversal component rejected: %r' % (what, path))
    return path


def validate_snapshot_data(data: Dict[str, bytes]) -> Dict[str, bytes]:
    if not isinstance(data, dict):
        raise SandboxError('snapshot must be a mapping')
    if len(data) > SNAPSHOT_MAX_FILES:
        raise SandboxError('snapshot has too many files: %d > %d' % (len(data), SNAPSHOT_MAX_FILES))
    total = 0
    clean: Dict[str, bytes] = {}
    for rel in data:
        validate_rel_path(rel, what='snapshot path')
        blob = data[rel]
        if not isinstance(blob, (bytes, bytearray)):
            raise SandboxError('snapshot value must be bytes: %s' % rel)
        if len(blob) > SNAPSHOT_MAX_FILE_BYTES:
            raise SandboxError('snapshot file too large: %s (%d > %d)' % (rel, len(blob), SNAPSHOT_MAX_FILE_BYTES))
        total += len(blob)
        if total > SNAPSHOT_MAX_BYTES:
            raise SandboxError('snapshot too large: %d > %d bytes' % (total, SNAPSHOT_MAX_BYTES))
        clean[rel] = bytes(blob)
    return clean


def decode_snapshot_b64(snapshot: Dict[str, Any]) -> Dict[str, bytes]:
    if not isinstance(snapshot, dict):
        raise SandboxError('snapshot must be a mapping of path -> base64 string')
    decoded: Dict[str, bytes] = {}
    for rel, b64 in snapshot.items():
        if not isinstance(b64, str):
            raise SandboxError('snapshot value must be a base64 string: %s' % rel)
        try:
            decoded[rel] = base64.b64decode(b64.encode('ascii'), validate=True)
        except (binascii.Error, ValueError, UnicodeEncodeError):
            raise SandboxError('invalid base64 for %s' % rel)
    return decoded


def _check_plain(value: Any, what: str, max_len: int) -> str:
    if not isinstance(value, str) or not value:
        raise SandboxError('%s must be a non-empty string' % what)
    if len(value) > max_len:
        raise SandboxError('%s too long (>%d)' % (what, max_len))
    if '\x00' in value:
        raise SandboxError('%s contains NUL' % what)
    return value


class _Plan:
    __slots__ = ('steps', 'step_timeout', 'overall_default')

    def __init__(self, steps: List[List[str]], step_timeout: float, overall_default: float):
        self.steps = steps
        self.step_timeout = step_timeout
        self.overall_default = overall_default


def build_plan(skill_id: Any, args: Any) -> _Plan:
    if not isinstance(skill_id, str):
        raise SandboxError('skill_id must be a string')
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise SandboxError('args must be an object')

    def timeout_number(default: float) -> float:
        value = args.get('timeout_seconds', default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SandboxError('timeout_seconds must be a number')
        if not (SKILL_TIMEOUT_MIN <= value <= SKILL_TIMEOUT_MAX):
            raise SandboxError('timeout_seconds must be within [%d, %d]' % (SKILL_TIMEOUT_MIN, SKILL_TIMEOUT_MAX))
        return float(value)

    def command() -> str:
        return _check_plain(args.get('command'), 'command', _MAX_COMMAND_LEN)

    def code_or_path(lang: str) -> List[str]:
        code = args.get('code')
        path = args.get('path')
        if (code is None) == (path is None):
            raise SandboxError('%s requires exactly one of code or path' % lang)
        if lang == 'exec.python':
            cli, exec_flag = 'python3', '-c'
        else:
            cli, exec_flag = 'node', '-e'
        extra = _job_args()
        if code is not None:
            _check_plain(code, 'code', _MAX_CODE_LEN)
            return [cli, exec_flag, code] + extra
        validate_rel_path(path, what='%s path' % lang)
        return [cli, path] + extra

    def job_args() -> List[str]:
        raw = args.get('args', [])
        if not isinstance(raw, list) or len(raw) > _MAX_ARGS:
            raise SandboxError('args must be a list of at most %d strings' % _MAX_ARGS)
        return [_check_plain(item, 'args item', _MAX_ARG_LEN) for item in raw]

    _job_args = job_args

    def optional_rel(key: str) -> str:
        value = args.get(key, '.')
        return validate_rel_path(value, allow_root_dot=True, what=key)

    if skill_id == 'exec.shell' or skill_id == 'exec.background':
        cmd = command()
        timeout = timeout_number(SKILL_TIMEOUT_DEFAULT)
        return _Plan([['/bin/sh', '-lc', cmd]], timeout, timeout + OVERALL_MARGIN_SECONDS)
    if skill_id == 'exec.python':
        timeout = timeout_number(SKILL_TIMEOUT_DEFAULT)
        return _Plan([code_or_path('exec.python')], timeout, timeout + OVERALL_MARGIN_SECONDS)
    if skill_id == 'exec.node':
        timeout = timeout_number(SKILL_TIMEOUT_DEFAULT)
        return _Plan([code_or_path('exec.node')], timeout, timeout + OVERALL_MARGIN_SECONDS)
    if skill_id == 'test.run':
        runner = args.get('runner')
        if runner not in TEST_RUNNERS:
            raise SandboxError('runner must be one of %s' % (TEST_RUNNERS,))
        path = optional_rel('path')
        filt = args.get('filter')
        if filt is not None:
            filt = _check_plain(filt, 'filter', 256)
        if runner == 'python_unittest':
            cmd = ['python3', '-m', 'unittest', 'discover', '-s', path, '-t', '/workspace']
            if filt:
                cmd += ['-k', filt]
        elif runner == 'node_test':
            cmd = ['node', '--test'] + (['--test-name-pattern',filt] if filt else []) + ([] if path == '.' else [path])
        else:
            cmd = ['npm', 'test', '--'] + ([filt] if filt else [])
        timeout = timeout_number(STEP_TIMEOUTS['test.run'])
        return _Plan([cmd], timeout, timeout + OVERALL_MARGIN_SECONDS)
    if skill_id == 'git.status':
        timeout = STEP_TIMEOUTS['git.status']
        steps = [['git'] + list(GIT_SAFE_CONFIG) + ['status', '--porcelain=v1', '-b']]
        return _Plan(steps, timeout, timeout + OVERALL_MARGIN_SECONDS)
    if skill_id == 'git.diff':
        timeout = STEP_TIMEOUTS['git.diff']
        cmd = ['git'] + list(GIT_SAFE_CONFIG) + ['diff', '--no-color', '--no-ext-diff', '--no-textconv']
        if args.get('staged'):
            cmd.append('--cached')
        return _Plan([cmd], timeout, timeout + OVERALL_MARGIN_SECONDS)
    if skill_id == 'git.commit':
        message = _check_plain(args.get('message'), 'message', _MAX_MESSAGE_LEN)
        raw_paths = args.get('paths', [])
        if not isinstance(raw_paths, list) or len(raw_paths) > _MAX_ARGS:
            raise SandboxError('paths must be a list of at most %d strings' % _MAX_ARGS)
        paths = [validate_rel_path(p, what='git path') for p in raw_paths]
        author_name = args.get('author_name')
        author_email = args.get('author_email')
        identity: List[str] = []
        if author_name is not None:
            _check_plain(author_name, 'author_name', 200)
            if _CONTROL_RE.search(author_name) or author_name.startswith('-') or '=' in author_name:
                raise SandboxError('author_name rejected')
            identity += ['-c', 'user.name=%s' % author_name]
        else:
            identity += ['-c', 'user.name=%s' % DEFAULT_GIT_NAME]
        if author_email is not None:
            _check_plain(author_email, 'author_email', 200)
            if _CONTROL_RE.search(author_email) or author_email.startswith('-') or '=' in author_email:
                raise SandboxError('author_email rejected')
            identity += ['-c', 'user.email=%s' % author_email]
        else:
            identity += ['-c', 'user.email=%s' % DEFAULT_GIT_EMAIL]
        safe = list(GIT_SAFE_CONFIG)
        steps: List[List[str]] = [['git'] + safe + ['init', '-q']]
        if paths:
            steps.append(['git'] + safe + ['add', '--'] + paths)
        else:
            steps.append(['git'] + safe + ['add', '--all'])
        steps.append(['git'] + safe + identity + ['commit', '-m', message])
        timeout = STEP_TIMEOUTS['git.commit']
        return _Plan(steps, timeout, timeout * len(steps) + OVERALL_MARGIN_SECONDS)
    if skill_id in ('code.format','format.run','lint.run','type.check','build.run','pkg.list','pkg.install','test.single','test.coverage','code.rename','refactor.rename','refactor.extract','refactor.dead_code'):
        from .phase2_exec import build_phase2_plan
        planned=build_phase2_plan(skill_id,args)
        return _Plan(planned['steps'],planned['step_timeout'],planned['overall_default'])
    raise SandboxError('unsupported skill_id: %r' % skill_id)


class _Job:
    __slots__ = ('job_id', 'container', 'volume', 'keeper', 'importer', 'exporter', 'state',
                 'running', 'procs', 'stop_requested', 'task', 'container_killed')

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.container = CONTAINER_PREFIX + job_id
        self.volume = CONTAINER_PREFIX + 'vol-' + job_id
        self.keeper = CONTAINER_PREFIX + 'k-' + job_id
        self.importer = CONTAINER_PREFIX + 'i-' + job_id
        self.exporter = CONTAINER_PREFIX + 'o-' + job_id
        self.state = 'queued'
        self.running: set = set()
        self.procs: set = set()
        self.stop_requested = False
        self.task: Optional[asyncio.Task] = None
        self.container_killed = False


class DockerSandbox:
    def __init__(self, image: str = IMAGE_DEFAULT, binary: str = '/usr/bin/docker',
                 max_active: int = MAX_ACTIVE_DEFAULT, phase2_runtime: bool = False):
        if not isinstance(image, str) or not image:
            raise ValueError('image must be a non-empty string')
        if not isinstance(binary, str) or not binary:
            raise ValueError('binary must be a non-empty string')
        if isinstance(max_active, bool) or not isinstance(max_active, int) or max_active < 1:
            raise ValueError('max_active must be a positive integer')
        self.phase2_runtime=bool(phase2_runtime)
        self.image = image
        self.binary = binary
        self.max_active = max_active
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._jobs: Dict[str, _Job] = {}
        self._results: Dict[str, Dict[str, Any]] = {}

    def limits(self) -> Dict[str, Any]:
        return {
            'output_per_stream_bytes': OUTPUT_LIMIT,
            'snapshot_max_bytes': SNAPSHOT_MAX_BYTES,
            'snapshot_max_files': SNAPSHOT_MAX_FILES,
            'snapshot_max_file_bytes': SNAPSHOT_MAX_FILE_BYTES,
            'skill_timeout_range_seconds': [SKILL_TIMEOUT_MIN, SKILL_TIMEOUT_MAX],
            'overall_max_seconds': OVERALL_MAX_SECONDS,
            'max_active': self.max_active,
            'helper_timeout_seconds': HELPER_TIMEOUT_SECONDS,
            'container_label': JOB_LABEL_KEY,
            'container': {
                'read_only': True, 'network': 'none', 'cap_drop': 'ALL',
                'no_new_privileges': True, 'pids_limit': 64, 'memory': '512m',
                'memory_swap': '512m', 'cpus': 1, 'user': SANDBOX_USER,
                'tmpfs_tmp': 'rw,noexec,nosuid,size=16m',
                'log_driver': 'none',
                'ulimits': {'nofile': 128, 'core': 0},
                'watchdog_pid1': 'timeout -k 2 <seconds>',
            },
            'volume': {'type': 'tmpfs', 'opts': VOLUME_TMPFS},
            'keeper': {'pid1': 'sleep %d' % KEEPER_SECONDS, 'seconds': KEEPER_SECONDS,
                       'mount': '/workspace', 'across': ['import', 'exec', 'export']},
        }

    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_active)
        return self._semaphore

    @staticmethod
    def _job_key(job_id: Any) -> str:
        if not isinstance(job_id, str):
            raise SandboxError('job_id must be a UUID string')
        try:
            parsed = uuid.UUID(job_id)
        except (ValueError, AttributeError, TypeError):
            raise SandboxError('job_id must be a valid UUID')
        canonical = str(parsed)
        if job_id != canonical:
            raise SandboxError('job_id must be the canonical lowercase UUID form')
        return canonical

    def _register(self, job_id: str) -> _Job:
        if job_id in self._jobs and self._jobs[job_id].state != 'done':
            raise SandboxError('duplicate active job_id: %s' % job_id)
        job = _Job(job_id)
        self._jobs[job_id] = job
        return job

    def _run_argv(self, name: str, volume: str, cmd: Sequence[str], *,
                  interactive: bool = False, detached: bool = False,
                  job_id: Optional[str] = None) -> List[str]:
        argv = [self.binary, 'run', '--rm', '--name', name]
        if interactive:
            argv.append('--interactive')
        if detached:
            argv.append('-d')
        argv += ['-v', '%s:/workspace:nocopy' % volume, '-w', '/workspace']
        argv += list(EXEC_FLAGS)
        argv += LOG_DRIVER_FLAGS
        argv += list(ULIMIT_FLAGS)
        if job_id:
            argv += ['--label', '%s=%s' % (JOB_LABEL_KEY, job_id)]
        if self.phase2_runtime:
            argv+=['-e','PYTHONPATH=/tmp/harmonika-deps/python','-e','NODE_PATH=/tmp/harmonika-deps/node/node_modules:/opt/harmonika/node/node_modules','-e','PIP_NO_INDEX=1','-e','PIP_DISABLE_PIP_VERSION_CHECK=1']
        argv.append(self.image)
        argv += list(cmd)
        return argv

    def _volume_create_argv(self, volume: str) -> List[str]:
        return [self.binary, 'volume', 'create', '--driver', 'local', '--name', volume,
                '--opt', 'type=tmpfs', '--opt', 'device=tmpfs', '--opt', 'o=' + VOLUME_TMPFS]

    def _import_argv(self, job: _Job) -> List[str]:
        return self._run_argv(
            job.importer, job.volume,
            watchdog_wrap(HELPER_TIMEOUT_SECONDS,
                          ['python3', '/opt/harmonika/volume_io.py', '--import']),
            interactive=True, job_id=job.job_id)

    def _export_argv(self, job: _Job) -> List[str]:
        return self._run_argv(
            job.exporter, job.volume,
            watchdog_wrap(HELPER_TIMEOUT_SECONDS,
                          ['python3', '/opt/harmonika/volume_io.py', '--export']),
            job_id=job.job_id)

    def _keeper_argv(self, job: _Job) -> List[str]:
        return self._run_argv(job.keeper, job.volume, ['sleep', str(KEEPER_SECONDS)],
                              detached=True, job_id=job.job_id)

    async def capabilities(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            'binary': self.binary,
            'image': self.image,
            'docker_available': False,
            'image_available': False,
            'ready': False,
            'phase2_ready':False,
            'server_version': None,
            'error': None,
        }
        try:
            rc, so, se = await self._run_captured(
                [self.binary, 'version', '--format', '{{.Server.Version}}'], timeout=10)
        except SandboxError as exc:
            out['error'] = str(exc)
            return out
        if rc != 0:
            out['error'] = 'docker version failed: %s' % se.decode('utf-8', 'replace')[:300]
            return out
        out['docker_available'] = True
        out['server_version'] = so.decode('utf-8', 'replace').strip()
        try:
            rc2, _, se2 = await self._run_captured([self.binary, 'image', 'inspect', self.image], timeout=15)
        except SandboxError as exc:
            out['error'] = str(exc)
            return out
        out['image_available'] = rc2 == 0
        if not out['image_available']:
            out['error'] = 'image %s not present: %s' % (self.image, se2.decode('utf-8', 'replace')[:300])
        out['ready'] = out['docker_available'] and out['image_available']
        if out['ready'] and self.phase2_runtime:
            try:
                rc,raw,_=await self._run_captured([self.binary,'image','inspect','--format','{{json .Config.Labels}}',self.image],timeout=10)
                labels=json.loads(raw or b'null')
                out['phase2_ready']=rc==0 and isinstance(labels,dict) and labels.get('harmonika.phase2')=='1'
            except (SandboxError,ValueError):pass
        return out

    async def run(self, snapshot: Dict[str, str], skill_id: str, args: Dict[str, Any],
                  job_id: str, on_output: Optional[OutputCallback] = None,
                  deadline_seconds: Optional[float] = None) -> Dict[str, Any]:
        started = time.monotonic()
        try:
            jid = self._job_key(job_id)
        except SandboxError as exc:
            return self._result(job_id, skill_id, started, status='failed',
                                error='invalid_request', detail=str(exc))
        try:
            data = validate_snapshot_data(decode_snapshot_b64(snapshot))
            plan = build_plan(skill_id, args)
            if deadline_seconds is not None:
                if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)):
                    raise SandboxError('deadline_seconds must be a number')
                if not (1 <= float(deadline_seconds) <= OVERALL_MAX_SECONDS):
                    raise SandboxError('deadline_seconds must be within [1, %d]' % int(OVERALL_MAX_SECONDS))
        except SandboxError as exc:
            return self._result(job_id, skill_id, started, status='failed',
                                error='invalid_request', detail=str(exc))
        try:
            job = self._register(jid)
        except SandboxError as exc:
            return self._result(job_id, skill_id, started, status='failed',
                                error='duplicate_job_id', detail=str(exc))
        if skill_id == 'exec.background':
            return self._start_background(job, skill_id, data, plan, on_output, deadline_seconds, started)
        return await self._execute(job, skill_id, data, plan, on_output, deadline_seconds, started)

    def _start_background(self, job: _Job, skill_id: str, data: Dict[str, bytes], plan: _Plan,
                          on_output: Optional[OutputCallback], deadline_seconds: Optional[float],
                          started: float) -> Dict[str, Any]:
        async def _runner() -> None:
            result = await self._execute(job, skill_id, data, plan, on_output, deadline_seconds, started)
            self._results[job.job_id] = result

        job.task = asyncio.ensure_future(_runner())
        return {
            'job_id': job.job_id,
            'skill_id': skill_id,
            'status': 'started',
            'limits': self.limits(),
            'note': 'poll job_result(job_id) for the final result',
        }

    async def job_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        jid = self._job_key(job_id)
        job = self._jobs.get(jid)
        if job is not None and job.task is not None:
            try:
                await asyncio.shield(job.task)
            except asyncio.CancelledError:
                pass
        return self._results.get(jid)

    async def stop(self, job_id: str) -> bool:
        try:
            jid = self._job_key(job_id)
        except SandboxError:
            return False
        job = self._jobs.get(jid)
        if job is None or job.state == 'done':
            return False
        job.stop_requested = True
        for name in list(job.running):
            await self._kill_name(name)
        if job.task is not None:
            job.task.cancel()
        return True

    async def _kill_name(self, name: str) -> None:
        try:
            await self._run_captured([self.binary, 'kill', name], timeout=10)
        except Exception:
            pass

    def _kill_proc(self, proc: asyncio.subprocess.Process) -> None:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass

    async def _run_captured(self, argv: List[str], *, input_bytes: Optional[bytes] = None,
                            timeout: Optional[float] = None) -> Tuple[Optional[int], bytes, bytes]:
        stdin = asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL
        try:
            proc = await asyncio.create_subprocess_exec(
                argv[0], *argv[1:], stdin=stdin,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise SandboxError('docker CLI unavailable: %s' % exc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(input_bytes), timeout)
        except asyncio.TimeoutError:
            self._kill_proc(proc)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                pass
            raise SandboxError('docker CLI timeout: %s' % ' '.join(argv[:3]))
        return proc.returncode, out or b'', err or b''

    async def _run_streamed(self, argv: List[str], *, on_output: Optional[OutputCallback],
                            job: _Job, budget: float) -> Tuple[Optional[int], bytes, bytes, set, bool]:
        try:
            proc = await asyncio.create_subprocess_exec(
                argv[0], *argv[1:], stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise SandboxError('docker CLI unavailable: %s' % exc)
        job.procs.add(proc)
        job.running.add(job.container)
        timed_out = False
        bufs = {'stdout': bytearray(), 'stderr': bytearray()}
        overflow: set = set()

        async def emit(stream_name: str, chunk: bytes) -> None:
            if on_output is None:
                return
            payload = {'job_id': job.job_id, 'stream': stream_name,
                       'data': chunk.decode('utf-8', 'replace')}
            outcome = on_output(payload)
            if asyncio.iscoroutine(outcome):
                await outcome

        async def pump(stream: asyncio.StreamReader, stream_name: str) -> None:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                buf = bufs[stream_name]
                room = OUTPUT_LIMIT - len(buf)
                if room <= 0:
                    if stream_name not in overflow:
                        overflow.add(stream_name)
                        await self._kill_name(job.container)
                    continue
                take = chunk[:room]
                buf.extend(take)
                try:
                    await emit(stream_name, take)
                except Exception as exc:
                    raise SandboxError('output callback failed: %s' % exc)
                if len(take) < len(chunk):
                    if stream_name not in overflow:
                        overflow.add(stream_name)
                        await self._kill_name(job.container)

        streams = []
        if proc.stdout is not None:
            streams.append((proc.stdout, 'stdout'))
        if proc.stderr is not None:
            streams.append((proc.stderr, 'stderr'))
        pumps = [asyncio.ensure_future(pump(stream, name)) for stream, name in streams]
        pump_failure: Optional[SandboxError] = None

        try:
            if pumps:
                await asyncio.wait_for(asyncio.shield(asyncio.gather(*pumps)), max(0.05, budget))
        except asyncio.TimeoutError:
            timed_out = True
        except SandboxError as exc:
            pump_failure = exc
        except Exception as exc:
            pump_failure = SandboxError('output pump failed: %s' % exc)

        if timed_out or pump_failure is not None:
            await self._kill_name(job.container)
            self._kill_proc(proc)
        try:
            rc: Optional[int] = await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            rc = None
        if pumps:
            await asyncio.gather(*pumps, return_exceptions=True)
        job.procs.discard(proc)
        job.running.discard(job.container)
        if pump_failure is not None:
            raise pump_failure
        return rc, bytes(bufs['stdout']), bytes(bufs['stderr']), overflow, timed_out

    def _result(self, job_id: str, skill_id: str, started: float, **fields: Any) -> Dict[str, Any]:
        result = {
            'job_id': job_id,
            'skill_id': skill_id,
            'status': fields.pop('status'),
            'exit_code': fields.pop('exit_code', None),
            'stdout': fields.pop('stdout', ''),
            'stderr': fields.pop('stderr', ''),
            'warnings': fields.pop('warnings', []),
            'duration_ms': int((time.monotonic() - started) * 1000),
            'limits': self.limits(),
        }
        result.update(fields)
        return result

    def _compute_deadline(self, started: float, deadline_seconds: Optional[float],
                          plan: _Plan) -> float:
        if deadline_seconds is not None:
            return started + min(float(deadline_seconds), OVERALL_MAX_SECONDS)
        return started + min(plan.overall_default, OVERALL_MAX_SECONDS)

    async def _cleanup(self, job: _Job) -> None:
        for proc in list(job.procs):
            self._kill_proc(proc)
        job.procs.clear()
        for name in (job.container, job.importer, job.exporter, job.keeper):
            try:
                await self._run_captured([self.binary, 'rm', '-f', name], timeout=15)
            except Exception:
                pass
        job.running.clear()
        try:
            await self._run_captured([self.binary, 'volume', 'rm', '-f', job.volume], timeout=15)
        except Exception:
            pass

    def _validate_export(self, stdout: bytes) -> Tuple[Dict[str, str], List[str]]:
        try:
            payload = json.loads(stdout.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxError('export returned invalid JSON: %s' % exc)
        if not isinstance(payload, dict) or not isinstance(payload.get('snapshot'), dict):
            raise SandboxError('export payload missing snapshot mapping')
        decoded = decode_snapshot_b64(payload['snapshot'])
        clean = validate_snapshot_data(decoded)
        clean_b64 = {rel: base64.b64encode(clean[rel]).decode('ascii') for rel in sorted(clean)}
        warnings = payload.get('warnings')
        if not isinstance(warnings, list):
            warnings = []
        return clean_b64, [str(w)[:500] for w in warnings]

    async def _tracked_captured(self, job: _Job, name: str, argv: List[str], *,
                                input_bytes: Optional[bytes], timeout: float
                                ) -> Tuple[Optional[int], bytes, bytes]:
        job.running.add(name)
        try:
            return await self._run_captured(argv, input_bytes=input_bytes, timeout=timeout)
        finally:
            job.running.discard(name)

    async def _keeper_alive(self, job: _Job) -> bool:
        try:
            rc, out, _ = await self._run_captured(
                [self.binary, 'inspect', '-f', '{{.State.Running}}', job.keeper], timeout=10)
        except SandboxError:
            return False
        return rc == 0 and out.decode('utf-8', 'replace').strip().lower() == 'true'

    async def _pipeline(self, job: _Job, data: Dict[str, bytes], plan: _Plan,
                        on_output: Optional[OutputCallback], deadline_seconds: Optional[float]
                        ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            'status': 'failed', 'exit_code': None, 'stdout': '', 'stderr': '',
            'warnings': [], 'snapshot': None, 'error': None, 'reason': None,
            'changed_state_unknown': False,
        }
        warnings: List[str] = out['warnings']
        deadline = self._compute_deadline(time.monotonic(), deadline_seconds, plan)

        def remaining(cap: Optional[float] = None) -> float:
            left = deadline - time.monotonic()
            if cap is not None:
                left = min(left, cap)
            return left

        job.state = 'running'
        if job.stop_requested:
            out['status'] = 'stopped'
            return out

        try:
            rc, _, err = await self._run_captured(
                self._volume_create_argv(job.volume), timeout=max(0.05, remaining(15.0)))
        except SandboxError as exc:
            rc, err = None, str(exc).encode()
        if rc != 0:
            out['error'] = 'volume_create_failed'
            out['reason'] = err.decode('utf-8', 'replace')[:500]
            return out

        if job.stop_requested:
            out['status'] = 'stopped'
            return out

        try:
            k_rc, _, k_err = await self._tracked_captured(
                job, job.keeper, self._keeper_argv(job),
                input_bytes=None, timeout=max(0.05, remaining(15.0)))
        except SandboxError as exc:
            k_rc, k_err = None, str(exc).encode()
        if job.stop_requested and k_rc != 0:
            out['status'] = 'stopped'
            return out
        if k_rc != 0:
            out['error'] = 'keeper_failed'
            out['reason'] = k_err.decode('utf-8', 'replace')[:500]
            return out

        if job.stop_requested:
            out['status'] = 'stopped'
            return out

        payload = json.dumps({'snapshot': {rel: base64.b64encode(data[rel]).decode('ascii')
                                           for rel in sorted(data)}}).encode('utf-8')
        try:
            imp_rc, _, imp_err = await self._tracked_captured(
                job, job.importer, self._import_argv(job),
                input_bytes=payload, timeout=max(0.05, remaining(60.0)))
        except SandboxError as exc:
            imp_rc, imp_err = None, str(exc).encode()
        for line in imp_err.decode('utf-8', 'replace').splitlines()[:20]:
            if line.strip():
                warnings.append('import: %s' % line[:300])
        if imp_rc != 0:
            out['error'] = 'import_failed'
            out['reason'] = imp_err.decode('utf-8', 'replace')[:500]
            return out

        if not await self._keeper_alive(job):
            out['error'] = 'keeper_lost'
            out['reason'] = 'keeper_lost'
            out['changed_state_unknown'] = True
            return out

        timed_out = overflowed = stopped = step_error = False
        exit_code: Optional[int] = None
        stdout_acc = bytearray()
        stderr_acc = bytearray()
        for cmd in plan.steps:
            left = remaining(plan.step_timeout)
            if left <= 0:
                timed_out = True
                break
            if job.stop_requested:
                stopped = True
                break
            if self.phase2_runtime:
                cmd=['/bin/sh','-c','python3 /opt/harmonika/phase2_exec.py prepare-env >/dev/null && exec "$@"','harmonika-env',*cmd]
            argv = self._run_argv(job.container, job.volume,
                                  watchdog_wrap(plan.step_timeout, cmd), job_id=job.job_id)
            try:
                rc, out_b, err_b, overflow, timed = await self._run_streamed(
                    argv, on_output=on_output, job=job, budget=left)
            except SandboxError as exc:
                step_error = True
                out['error'] = str(exc)
                break
            stdout_acc += out_b[:OUTPUT_LIMIT]
            stderr_acc += err_b[:OUTPUT_LIMIT]
            if timed or (rc==124 and not overflow):
                timed_out = True
                break
            if overflow:
                overflowed = True
                break
            if job.stop_requested:
                stopped = True
                break
            exit_code = rc
            if rc != 0:
                break
        if timed_out or overflowed:
            await self._kill_name(job.container)
        out['exit_code'] = exit_code
        out['stdout'] = stdout_acc.decode('utf-8', 'replace')
        out['stderr'] = stderr_acc.decode('utf-8', 'replace')
        if timed_out:
            out['status'] = 'timeout'
            out['reason'] = 'deadline_exceeded'
        elif overflowed:
            out['status'] = 'failed'
            out['reason'] = 'output_overflow'
        elif stopped:
            out['status'] = 'stopped'
        elif step_error:
            out['status'] = 'failed'
            out['reason'] = out['reason'] or 'sandbox_error'
        elif exit_code is None:
            out['status'] = 'failed'
            out['reason'] = out['reason'] or 'no_step_completed'
        elif exit_code != 0:
            out['status'] = 'failed'
            out['reason'] = 'nonzero_exit'
        else:
            out['status'] = 'completed'

        if job.stop_requested or time.monotonic() >= deadline:
            out['changed_state_unknown'] = True
            out['reason'] = out['reason'] or 'export_not_attempted'
            return out
        if not await self._keeper_alive(job):
            out['changed_state_unknown'] = True
            out['reason'] = out['reason'] or 'keeper_lost'
            return out
        try:
            exp_rc, exp_out, exp_err = await self._tracked_captured(
                job, job.exporter, self._export_argv(job),
                input_bytes=None, timeout=max(0.05, remaining(60.0)))
        except SandboxError as exc:
            exp_rc, exp_out, exp_err = None, b'', str(exc).encode()
        for line in exp_err.decode('utf-8', 'replace').splitlines()[:20]:
            if line.strip():
                warnings.append('export: %s' % line[:300])
        if exp_rc == 0:
            try:
                snapshot, export_warnings = self._validate_export(exp_out)
                out['snapshot'] = snapshot
                warnings.extend(export_warnings)
            except SandboxError as exc:
                out['changed_state_unknown'] = True
                out['reason'] = out['reason'] or 'export_failed'
                warnings.append('export invalid: %s' % exc)
        else:
            out['changed_state_unknown'] = True
            out['reason'] = out['reason'] or 'export_failed'
        return out

    async def _execute(self, job: _Job, skill_id: str, data: Dict[str, bytes], plan: _Plan,
                       on_output: Optional[OutputCallback], deadline_seconds: Optional[float],
                       started: float) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            'status': 'stopped', 'exit_code': None, 'stdout': '', 'stderr': '',
            'warnings': [], 'reason': 'stop_requested', 'changed_state_unknown': True,
        }
        try:
            async with self._sem():
                fields = await self._pipeline(job, data, plan, on_output, deadline_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup(job)
            job.state = 'done'
        result = self._result(job.job_id, skill_id, started,
                              status=fields['status'],
                              exit_code=fields['exit_code'],
                              stdout=fields['stdout'],
                              stderr=fields['stderr'],
                              warnings=fields['warnings'])
        if fields.get('snapshot') is not None:
            result['snapshot'] = fields['snapshot']
        if fields.get('error'):
            result['error'] = fields['error']
        if fields.get('reason'):
            result['reason'] = fields['reason']
        if fields.get('changed_state_unknown'):
            result['changed_state_unknown'] = True
        return result
