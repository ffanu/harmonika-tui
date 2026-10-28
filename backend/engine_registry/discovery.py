"""Operator-managed async auto-discovery for Harmonika engine skill plugins."""

from contextlib import closing
import ast
import asyncio
import base64
import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .failure_log import FailureLog
from . import failure_log

__all__ = ["Discovery", "SanitizedError", "SkillNotRegistered"]

CODE_OK = "OK"
CODE_BUSY = "E_SCAN_BUSY"
CODE_CLOSED = "E_CLOSED"
CODE_ROOT = "E_ROOT_UNREADABLE"
CODE_NOT_REGISTERED = "E_NOT_REGISTERED"
CODE_MISSING_SKILL = "E_MISSING_SKILL_PY"
CODE_MISSING_MANIFEST = "E_MISSING_MANIFEST"
CODE_MANIFEST_INVALID = "E_MANIFEST_INVALID"
CODE_DIGEST_MISMATCH = "E_DIGEST_MISMATCH"
CODE_SYMLINK = "E_SYMLINK"
CODE_BOUNDS = "E_BOUNDS"
CODE_SYNTAX = "E_SYNTAX"
CODE_CONTRACT = "E_CONTRACT"
CODE_STATIC_UNSAFE = "E_CONTRACT_STATIC"
CODE_METADATA_INVALID = "E_METADATA_INVALID"
CODE_TIMEOUT = "E_TIMEOUT"
CODE_PLUGIN = "E_PLUGIN"
CODE_OUTPUT = "E_OUTPUT"
CODE_PAYLOAD = "E_PAYLOAD"
CODE_DUPLICATE_ID = "E_DUPLICATE_ID"
CODE_RESERVED_ID = "E_RESERVED_ID"
CODE_DATABASE = "E_DATABASE"
CODE_RUNTIME = "E_RUNTIME"

_CHILD_ERROR_CODES = frozenset({CODE_PLUGIN, CODE_OUTPUT})
_SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SKILL_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
# Registry error codes mapped onto the fixed technical failure-log allowlist.
_FAILURE_ERROR_TYPES = {
    CODE_PAYLOAD: "invalid_input",
    CODE_PLUGIN: "skill_exception",
    CODE_TIMEOUT: "skill_timeout",
    CODE_OUTPUT: "invalid_output",
    "E_CANCELLED": "cancelled",
}
_BOUNDARY_KEYS = ("max_input_tokens", "max_output_tokens", "max_chat_messages", "max_tool_calls")
_UNSAFE_TOP_LEVEL = (ast.While, ast.For, ast.AsyncFor, ast.Raise, ast.If, ast.Try, ast.With, ast.AsyncWith)

_RUNNER = r'''
import asyncio,base64,inspect,json,os,resource,sys
def emit(obj):
    try: blob=json.dumps(obj,ensure_ascii=True)
    except Exception: blob='{"ok": false, "error_code": "E_OUTPUT"}'
    try:
        sys.stdout.write(blob)
        sys.stdout.flush()
    except Exception: pass
def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    cfg=json.loads(sys.stdin.read())
    for name,lim in cfg.get("rlimits",{}).items():
        try: resource.setrlimit(getattr(resource,name),(lim[0],lim[1]))
        except Exception: pass
    try: sys.stderr=open(os.devnull,"w")
    except Exception: pass
    if sys.platform.startswith('linux') and os.geteuid()==0:
        # Child-only privilege drop. Captured source/result use inherited fds.
        os.setgroups([]);os.setgid(65534);os.setuid(65534)
    src=base64.b64decode(cfg["source"])
    ns={"__name__":"harmonika_skill","__file__":"<captured>"}
    try:
        exec(compile(src,"<harmonika-skill>","exec"),ns)
        for name,arity in (('Execute',1),('GetSkillIdentity',0),('GetContextRequirements',0)):
            fn=ns.get(name)
            if not callable(fn) or inspect.iscoroutinefunction(fn)!=(name=='Execute'):
                emit({'ok':False,'error_code':'E_PLUGIN'});return
            params=list(inspect.signature(fn).parameters.values())
            if len(params)!=arity or any(p.kind!=inspect.Parameter.POSITIONAL_OR_KEYWORD or p.default!=inspect.Parameter.empty for p in params):
                emit({'ok':False,'error_code':'E_PLUGIN'});return
        if cfg["mode"]=="metadata":
            emit({"ok":True,"identity":ns["GetSkillIdentity"](),"context":ns["GetContextRequirements"]()})
        else:
            emit({"ok":True,"result":asyncio.run(ns["Execute"](cfg["payload"]))})
    except Exception:
        emit({"ok":False,"error_code":"E_PLUGIN"})
try: main()
except Exception: pass
'''


class SanitizedError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SkillNotRegistered(SanitizedError):
    pass


@dataclass
class _Entry:
    descriptor: Dict[str, Any]
    source: bytes


@dataclass
class _Candidate:
    directory: str
    source: bytes
    digest: str
    hash_verified: bool
    identity: Optional[Dict[str, Any]]
    context: Optional[Dict[str, Any]]
    latency_ms: Optional[float]
    code: Optional[str] = None

    def skill_id(self) -> str:
        if isinstance(self.identity, dict):
            value = self.identity.get("id")
            if isinstance(value, str) and _SKILL_ID.match(value):
                return value
        return ""


class Discovery:
    """Scans an operator-managed plugin directory into an atomic registry snapshot.

    Trust model: plugins are operator approved via filesystem trust plus a
    pinned sha256 in manifest.json. Verification records hash status only and
    never claims signature verification. Plugin code is statically validated
    and executed exclusively in bounded, sanitized, private-cwd subprocesses;
    the parent engine never imports plugin code.
    """

    def __init__(
        self,
        root,
        database,
        reserved_ids=(),
        *,
        metadata_timeout: float = 2.0,
        execute_timeout: float = 10.0,
        output_cap: int = 262144,
        max_plugins: int = 3072,
        max_file_bytes: int = 1_000_000,
        max_total_bytes: int = 64_000_000,
        memory_limit: int = 192 * 1024 * 1024,
        scan_timeout: float = 30.0,
        identity_policy=None,
    ):
        self._root = os.path.abspath(os.fspath(root))
        self._identity_policy=identity_policy or (lambda identity:True)
        self._scan_timeout=float(scan_timeout)
        self._metadata_cache={}
        self._generation=0
        self._scan_event=None
        self._scan_loop=None
        self._waiters=0
        self._source_leases={}
        self._source_lease_lock=threading.Lock()
        self._database = os.path.abspath(os.fspath(database))
        self._reserved = frozenset(str(item) for item in reserved_ids)
        self._metadata_timeout = float(metadata_timeout)
        self._execute_timeout = float(execute_timeout)
        self._output_cap = int(output_cap)
        self._max_plugins = int(max_plugins)
        self._max_file_bytes = int(max_file_bytes)
        self._max_total_bytes = int(max_total_bytes)
        self._memory_limit = int(memory_limit)
        self._snapshot: Dict[str, _Entry] = {}
        self._last_summary: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()
        self._scan_guard = threading.Lock()
        self._closed = False
        self._ensure_schema()
        info=os.stat(self._database);self._db_identity=(info.st_dev,info.st_ino)
        self._failure_log=FailureLog(self._database)

    async def refresh(self, *, wait=False, timeout=10.0) -> Dict[str, Any]:
        """Run one bounded scan in a worker thread and swap the snapshot."""
        if self._closed:
            raise SanitizedError(CODE_CLOSED)
        event=self._event()
        if not self._scan_guard.acquire(blocking=False):
            if wait:return await self.wait_ready(timeout=timeout)
            busy = dict(self.summary())
            busy["scanning"] = True
            busy["result"] = CODE_BUSY
            return busy
        event.clear()
        try:
            # Shield the worker so cancellation cannot release its guard while the
            # same scanner thread is still writing SQLite or replacing caches.
            task=asyncio.create_task(asyncio.to_thread(self._scan_sync))
            try:result=await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            if not self._closed:
                with self._lock:
                    self._snapshot = result["entries"]
                    self._last_summary = dict(result["summary"])
            return dict(result["summary"])
        finally:
            self._scan_guard.release()
            event.set()

    def _event(self):
        loop=asyncio.get_running_loop()
        if self._scan_event is None:
            self._scan_event=asyncio.Event();self._scan_event.set();self._scan_loop=loop
        elif self._scan_loop is not loop:raise SanitizedError(CODE_RUNTIME)
        return self._scan_event

    async def wait_ready(self, *, timeout=10.0):
        """Bounded async wait for publication; never triggers a scan or retries a tool."""
        if self._closed:raise SanitizedError(CODE_CLOSED)
        if not self._scan_guard.locked():return self.summary()
        if self._waiters>=16:raise SanitizedError(CODE_BUSY)
        self._waiters+=1;deadline=asyncio.get_running_loop().time()+timeout
        try:
            while self._scan_guard.locked():
                if self._closed:raise SanitizedError(CODE_CLOSED)
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:raise SanitizedError(CODE_BUSY)
                try:await asyncio.wait_for(self._event().wait(),remaining)
                except asyncio.TimeoutError:raise SanitizedError(CODE_BUSY) from None
            if self._closed:raise SanitizedError(CODE_CLOSED)
            return self.summary()
        finally:self._waiters-=1

    def descriptors(self) -> List[Dict[str, Any]]:
        with self._lock:
            snapshot = list(self._snapshot.values())
        ordered = sorted(snapshot, key=lambda entry: entry.descriptor["id"])
        return [copy.deepcopy(entry.descriptor) for entry in ordered]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            current = self._last_summary
        out=copy.deepcopy(current) if current is not None else self._load_summary()
        out['scanning']=self._scan_guard.locked()
        out['waiters']=self._waiters
        if out['scanning']:out.update(status='scanning',result=CODE_BUSY,retry_after_ms=100)
        return out

    async def execute(self, skill_id: str, payload: Any) -> Any:
        if self._closed:
            raise SanitizedError(CODE_CLOSED)
        await self.wait_ready()
        with self._lock:
            entry = self._snapshot.get(str(skill_id))
        if entry is None:
            raise SkillNotRegistered(CODE_NOT_REGISTERED)
        from .schema import validate,SchemaError
        input_started=time.monotonic()
        try:
            encoded=json.dumps(payload,allow_nan=False).encode()
            # Conservative byte upper bounds; token declarations are not a tokenizer.
            if len(encoded)>min(65536,entry.descriptor['context']['max_input_tokens']):raise SchemaError('skill_input_length')
            validate(payload,entry.descriptor['input_schema'])
        except (SchemaError,ValueError,TypeError):
            # Registered skill with rejected input: technical invalid_input event only.
            await self._record_failure(skill_id,'invalid_input',round((time.monotonic()-input_started)*1000,3))
            raise SanitizedError(CODE_PAYLOAD) from None
        started=time.monotonic();code=CODE_OK
        try:
            source=entry.source
            if not isinstance(source,bytes):
                digest=source.sha256
                with self._source_lease_lock:self._source_leases[digest]=self._source_leases.get(digest,0)+1
                try:source=await asyncio.to_thread(source.load_checked)
                finally:
                    with self._source_lease_lock:
                        self._source_leases[digest]-=1
                        if not self._source_leases[digest]:self._source_leases.pop(digest,None)
            blob = await self._run_child_async("execute", source, payload)
            result=blob.get('result')
            if type(result) is not dict:raise SanitizedError(CODE_OUTPUT)
            raw=json.dumps(result,allow_nan=False).encode()
            if len(raw)>min(self._output_cap,entry.descriptor['context']['max_output_tokens']):raise SanitizedError(CODE_OUTPUT)
            return result
        except SanitizedError as error:code=error.code;raise
        except asyncio.CancelledError:code='E_CANCELLED';raise
        except Exception:code=CODE_OUTPUT;raise SanitizedError(CODE_OUTPUT) from None
        finally:
            try:await asyncio.to_thread(self._record_execution,skill_id,entry.descriptor['sha256'],round((time.monotonic()-started)*1000,3),code)
            except SanitizedError:
                if code==CODE_OK:raise
                logging.getLogger('harmonika.discovery.audit').warning('execution_audit_unavailable')
            if code!=CODE_OK:
                # Technical failure event for registered skills; never affects the
                # error being propagated to the caller and never blocks cancellation.
                await self._record_failure(skill_id,_FAILURE_ERROR_TYPES.get(code,failure_log.DEFAULT_ERROR_TYPE),round((time.monotonic()-started)*1000,3))

    async def _record_failure(self,skill_id,error_type,latency):
        """Best-effort failure-log write; mechanical warning only on unavailability."""
        try:status=await asyncio.to_thread(self._failure_log.record,skill_id,error_type,latency)
        except Exception:status=failure_log.LOGGING_UNAVAILABLE
        if status==failure_log.LOGGING_UNAVAILABLE:
            logging.getLogger('harmonika.discovery.failure').warning('failure_log_unavailable')

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._snapshot = {}

    def _ensure_schema(self) -> None:
        directory = os.path.dirname(self._database)
        for item in [Path(self._database),*Path(self._database).parents]:
            if item.is_symlink():raise SanitizedError(CODE_DATABASE)
        if os.path.exists(self._database):
            info=os.stat(self._database)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid():raise SanitizedError(CODE_DATABASE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if not os.path.exists(self._database):
            fd=os.open(self._database,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        connection = sqlite3.connect(self._database, timeout=5.0)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables-{'engine_discovery_status','engine_discovery_summary','engine_discovery_execution','skill_failures'}:raise SanitizedError(CODE_DATABASE)
            os.chmod(self._database,0o600)
            connection.execute("CREATE TABLE IF NOT EXISTS engine_discovery_execution (skill_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, latency_ms REAL NOT NULL, error_code TEXT NOT NULL, updated_at TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS engine_discovery_status ("
                "root TEXT NOT NULL, directory TEXT NOT NULL, skill_id TEXT NOT NULL,"
                "sha256 TEXT, hash_verified INTEGER NOT NULL, error_code TEXT NOT NULL,"
                "audit_latency_ms REAL, updated_at TEXT NOT NULL,"
                "PRIMARY KEY (root, directory))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS engine_discovery_summary ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), root TEXT NOT NULL,"
                "finished_at TEXT, valid INTEGER NOT NULL, rejected INTEGER NOT NULL,"
                "error_codes TEXT NOT NULL, result TEXT NOT NULL, truncated INTEGER NOT NULL)"
            )
            connection.execute(failure_log.TABLE_DDL)
            connection.commit()
        finally:
            connection.close()

    def _connect_existing(self,timeout=1):
        try:
            info=os.lstat(self._database)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode & 0o077 or (info.st_dev,info.st_ino)!=self._db_identity:
                raise SanitizedError(CODE_DATABASE)
            return sqlite3.connect(Path(self._database).as_uri()+'?mode=rw',uri=True,timeout=timeout)
        except (OSError,sqlite3.Error):raise SanitizedError(CODE_DATABASE) from None

    def _default_summary(self) -> Dict[str, Any]:
        from .scanning import DEFAULT_CHUNK_SIZE,METADATA_CACHE_BUDGET,SPOOL_DISK_BUDGET
        return {
            "module_limit":self._max_plugins,
            "chunk_size":DEFAULT_CHUNK_SIZE,
            "source_budget_bytes":self._max_total_bytes,
            "metadata_budget_bytes":METADATA_CACHE_BUDGET,
            "source_spool_budget_bytes":SPOOL_DISK_BUDGET,
            "scan_budget_seconds":self._scan_timeout,
            "wait_timeout_seconds":10,
            "waiter_limit":16,
            "status": "ready",
            "generation":self._generation,
            "finished_at": None,
            "duration_ms": None,
            "scanned_dirs": 0,
            "valid": 0,
            "rejected": 0,
            "registered": 0,
            "skill_ids": [],
            "error_codes": {},
            "truncated": False,
            "result": "idle",
            "scanning": False,
        }

    def _load_summary(self) -> Dict[str, Any]:
        summary = self._default_summary()
        try:
            connection = self._connect_existing(timeout=5.0)
            try:
                row = connection.execute(
                    "SELECT finished_at, valid, rejected, error_codes, result, truncated"
                    " FROM engine_discovery_summary WHERE id = 1 AND root = ?",
                    (self._root,),
                ).fetchone()
            finally:
                connection.close()
        except (sqlite3.Error,SanitizedError):
            summary.update(status="degraded",result=CODE_DATABASE)
            return summary
        if row is None:
            return summary
        summary["finished_at"] = row[0]
        summary["valid"] = int(row[1])
        summary["rejected"] = int(row[2])
        try:
            summary["error_codes"] = dict(json.loads(row[3]))
        except (TypeError, ValueError):
            summary["error_codes"] = {}
        summary["result"] = row[4]
        summary["truncated"] = bool(row[5])
        summary["persisted"] = True
        summary["status"]="recovering"
        summary["registered"]=0 # source must be audited again; DB is not execution authority
        return summary

    def _record(self, directory, skill_id, digest, hash_verified, code, latency_ms, stamp):
        return (
            self._root,
            directory if directory and _SAFE_NAME.fullmatch(directory) else 'invalid-'+hashlib.sha256(str(directory).encode()).hexdigest()[:16],
            skill_id if skill_id and _SKILL_ID.match(skill_id) else "",
            digest if digest and _HEX64.match(digest) else None,
            1 if hash_verified else 0,
            code,
            round(float(latency_ms), 3) if latency_ms is not None else None,
            stamp,
        )

    def _scan_sync(self):
        from .scanning import scan
        return scan(self)

    def _persist(self, records: List[Tuple], summary: Dict[str, Any]) -> str:
        try:
            connection = self._connect_existing(timeout=5.0)
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM engine_discovery_execution WHERE skill_id NOT IN (SELECT skill_id FROM engine_discovery_execution ORDER BY updated_at DESC LIMIT 3072)")
                connection.execute("DELETE FROM engine_discovery_status WHERE root = ?", (self._root,))
                connection.executemany(
                    "INSERT OR REPLACE INTO engine_discovery_status"
                    " (root, directory, skill_id, sha256, hash_verified, error_code, audit_latency_ms, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    records,
                )
                connection.execute(
                    "INSERT OR REPLACE INTO engine_discovery_summary"
                    " (id, root, finished_at, valid, rejected, error_codes, result, truncated)"
                    " VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self._root,
                        summary["finished_at"],
                        summary["valid"],
                        summary["rejected"],
                        json.dumps(summary["error_codes"], sort_keys=True),
                        summary["result"],
                        1 if summary["truncated"] else 0,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        except (sqlite3.Error,SanitizedError):
            return CODE_DATABASE
        return CODE_OK

    def _read_regular(self,path,limit):
        # Directory ancestors are operator-managed; no link traversal is allowed.
        for parent in [Path(path).parent,*Path(path).parents]:
            if parent.is_symlink():raise SanitizedError(CODE_SYMLINK)
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as handle:
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise SanitizedError(CODE_SYMLINK)
            if info.st_mode & 0o022 or info.st_uid!=os.getuid():raise SanitizedError('E_UNTRUSTED_OWNER')
            if info.st_size>limit:raise SanitizedError(CODE_BOUNDS)
            blob=handle.read(limit+1)
            if len(blob)>limit:raise SanitizedError(CODE_BOUNDS)
            return blob

    def _inspect(self,name,path,budget):
        source=b'';digest='';verified=False;latency=None
        try:
            source=self._read_regular(os.path.join(path,'skill.py'),min(self._max_file_bytes,budget))
            manifest=self._read_regular(os.path.join(path,'manifest.json'),4096)
            pinned=self._parse_manifest(manifest)
            if pinned is None:raise SanitizedError(CODE_MANIFEST_INVALID)
            digest=hashlib.sha256(source).hexdigest()
            if digest!=pinned:raise SanitizedError(CODE_DIGEST_MISMATCH)
            verified=True
            code=_static_validate(source)
            if code:raise SanitizedError(code)
            if digest in self._metadata_cache:
                blob=copy.deepcopy(self._metadata_cache[digest]);latency=0.0
            else:
                started=time.monotonic();blob=self._run_child_sync('metadata',source,None);latency=(time.monotonic()-started)*1000
            if not isinstance(blob,dict):raise SanitizedError(blob if isinstance(blob,str) else CODE_PLUGIN)
            identity=blob.get('identity');context=blob.get('context')
            code=_validate_metadata(identity,context)
            if code:raise SanitizedError(code)
            self._metadata_cache[digest]=copy.deepcopy(blob)
            return _Candidate(name,source,digest,True,identity,context,latency,None)
        except FileNotFoundError as error:
            code=CODE_MISSING_MANIFEST if 'manifest.json' in str(error.filename) else CODE_MISSING_SKILL
        except SanitizedError as error:code=error.code
        except OSError:code=CODE_SYMLINK
        except Exception:code=CODE_RUNTIME
        return _Candidate(name,source,digest,verified,None,None,latency,code)

    def _record_execution(self,skill_id,digest,latency,code):
        try:
            with closing(self._connect_existing(timeout=1)) as db, db:
                db.execute('INSERT OR REPLACE INTO engine_discovery_execution VALUES(?,?,?,?,?)',(skill_id,digest,latency,code,time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
        except sqlite3.Error:raise SanitizedError(CODE_DATABASE) from None

    def _parse_manifest(self, data: bytes) -> Optional[str]:
        try:
            def unique(pairs):
                out={}
                for k,v in pairs:
                    if k in out:raise ValueError('duplicate')
                    out[k]=v
                return out
            obj = json.loads(data.decode("utf-8"),object_pairs_hook=unique)
        except (UnicodeDecodeError, ValueError):
            return None
        if not isinstance(obj, dict) or set(obj) != {"module_sha256"}:
            return None
        pinned = obj["module_sha256"]
        if not isinstance(pinned, str) or not _HEX64.match(pinned):
            return None
        return pinned

    def _child_env(self, cwd: str) -> Dict[str, str]:
        return {
            "PATH": _SAFE_PATH,
            "HOME": cwd,
            "TMPDIR": cwd,
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _rlimits(self, timeout: float) -> Dict[str, List[int]]:
        cpu = int(timeout) + 5
        return {
            "RLIMIT_AS": [self._memory_limit, self._memory_limit],
            "RLIMIT_CPU": [cpu, cpu],
            "RLIMIT_FSIZE": [self._output_cap, self._output_cap],
            "RLIMIT_NOFILE": [64, 64],
            "RLIMIT_CORE": [0,0],
        }

    def _request(self, mode: str, source: bytes, payload: Any) -> bytes:
        return json.dumps(
            {
                "mode": mode,
                "source": base64.b64encode(source).decode("ascii"),
                "payload": payload,
                "rlimits": self._rlimits(self._metadata_timeout if mode == "metadata" else self._execute_timeout),
                "output_cap": self._output_cap,
            }
        ).encode("utf-8")

    def _read_capped(self, out_file) -> bytes:
        out_file.seek(0)
        return out_file.read(self._output_cap + 1)

    def _interpret(self, blob: bytes, returncode: int) -> Any:
        if returncode != 0:
            return CODE_PLUGIN
        if len(blob) > self._output_cap:
            return CODE_OUTPUT
        try:
            obj = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return CODE_OUTPUT
        if not isinstance(obj, dict) or not isinstance(obj.get("ok"), bool):
            return CODE_OUTPUT
        if obj["ok"]:
            return obj
        code = obj.get("error_code")
        return code if code in _CHILD_ERROR_CODES else CODE_PLUGIN

    def _run_child_sync(self, mode: str, source: bytes, payload: Any) -> Any:
        request = self._request(mode, source, payload)
        timeout = self._metadata_timeout if mode == "metadata" else self._execute_timeout
        with tempfile.TemporaryDirectory(prefix="harmonika-skill-") as private:
            out_path = os.path.join(private, "out.json")
            with open(out_path, "w+b") as out_file:
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-I", "-c", _RUNNER],
                        stdin=subprocess.PIPE,
                        stdout=out_file,
                        stderr=subprocess.DEVNULL,
                        cwd=private,
                        env=self._child_env(private),
                    )
                except OSError:
                    return CODE_PLUGIN
                try:
                    process.communicate(request, timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    return CODE_TIMEOUT
                return self._interpret(self._read_capped(out_file), process.returncode)

    async def _run_child_async(self, mode: str, source: bytes, payload: Any) -> Dict[str, Any]:
        try:
            request = self._request(mode, source, payload)
        except (TypeError, ValueError):
            raise SanitizedError(CODE_PAYLOAD)
        timeout = self._execute_timeout
        with tempfile.TemporaryDirectory(prefix="harmonika-skill-") as private:
            out_path = os.path.join(private, "out.json")
            with open(out_path, "w+b") as out_file:
                try:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-I",
                        "-c",
                        _RUNNER,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=out_file,
                        stderr=asyncio.subprocess.DEVNULL,
                        cwd=private,
                        env=self._child_env(private),
                    )
                except OSError:
                    raise SanitizedError(CODE_PLUGIN)
                try:
                    await asyncio.wait_for(process.communicate(request), timeout)
                except (asyncio.TimeoutError,asyncio.CancelledError) as error:
                    process.kill()
                    await process.communicate()
                    if isinstance(error,asyncio.CancelledError):raise
                    raise SanitizedError(CODE_TIMEOUT)
                verdict = self._interpret(self._read_capped(out_file), process.returncode)
        if isinstance(verdict, str):
            raise SanitizedError(verdict)
        return verdict


def _zero_arg(node: ast.FunctionDef) -> bool:
    args = node.args
    return not node.decorator_list and not args.posonlyargs and not args.args and not args.vararg and not args.kwarg and not args.kwonlyargs and not args.defaults and not args.kw_defaults


def _one_arg(node: ast.AsyncFunctionDef) -> bool:
    args = node.args
    return (
        not node.decorator_list and not args.posonlyargs and len(args.args) == 1
        and not args.vararg
        and not args.kwarg
        and not args.kwonlyargs
        and not args.defaults
        and not args.kw_defaults
    )


def _static_validate(source: bytes) -> Optional[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return CODE_SYNTAX
    execute_defs = []
    sync_defs: Dict[str, ast.FunctionDef] = {}
    counts={}
    for node in tree.body:
        if isinstance(node, _UNSAFE_TOP_LEVEL):
            return CODE_STATIC_UNSAFE
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "Execute":
            execute_defs.append(node)
        elif isinstance(node, ast.FunctionDef):
            counts[node.name]=counts.get(node.name,0)+1
            sync_defs[node.name] = node
    if len(execute_defs) != 1 or not _one_arg(execute_defs[0]):
        return CODE_CONTRACT
    for name in ("GetSkillIdentity", "GetContextRequirements"):
        found = sync_defs.get(name)
        if found is None or counts.get(name)!=1 or not _zero_arg(found):
            return CODE_CONTRACT
    return None


def _validate_metadata(identity: Any, context: Any) -> Optional[str]:
    if not isinstance(identity, dict) or not isinstance(context, dict):
        return CODE_METADATA_INVALID
    try:
        if len(json.dumps(identity, ensure_ascii=True)) > 65536:
            return CODE_METADATA_INVALID
        if len(json.dumps(context, ensure_ascii=True)) > 65536:
            return CODE_METADATA_INVALID
    except (TypeError, ValueError):
        return CODE_METADATA_INVALID
    skill_id = identity.get("id")
    if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id) or len(skill_id)>100:
        return CODE_METADATA_INVALID
    name = identity.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 200 and not any(ord(c)<32 for c in name)):
        return CODE_METADATA_INVALID
    capabilities = identity.get("core_capabilities")
    if not isinstance(capabilities, list) or len(capabilities) > 32:
        return CODE_METADATA_INVALID
    for item in capabilities:
        if not isinstance(item, str) or not (1 <= len(item.strip()) <= 200):
            return CODE_METADATA_INVALID
    requirements = identity.get("requirements")
    if not isinstance(requirements, list) or len(requirements) > 32:
        return CODE_METADATA_INVALID
    for item in requirements:
        if not isinstance(item, str) or not (1 <= len(item.strip()) <= 200):
            return CODE_METADATA_INVALID
    try:
        from .schema import audit
        audit(identity.get('input_schema'))
        if identity['input_schema']['type']!='object':return CODE_METADATA_INVALID
    except Exception:return CODE_METADATA_INVALID
    if set(identity)-{'id','name','core_capabilities','requirements','input_schema'}:return CODE_METADATA_INVALID
    if not capabilities or requirements:return CODE_METADATA_INVALID # dependency injection not supported in pure runtime
    if set(context)!=set(_BOUNDARY_KEYS):return CODE_METADATA_INVALID
    if any(type(context[k]) is not int for k in _BOUNDARY_KEYS):return CODE_METADATA_INVALID
    if not 1<=context['max_input_tokens']<=65536 or not 1<=context['max_output_tokens']<=262144:return CODE_METADATA_INVALID
    if context['max_chat_messages']!=0 or context['max_tool_calls']!=0:return CODE_METADATA_INVALID
    return None
