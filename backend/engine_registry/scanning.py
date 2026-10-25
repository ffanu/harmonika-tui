"""Chunked lazy-source scanner for the Harmonika engine skill registry.

``scan(engine)`` is a drop-in replacement for ``Discovery._scan_sync`` and
returns the same ``{'entries', 'summary'}`` contract. Differences from the
legacy sweep:

* bounded chunked processing (default 64 modules per chunk with a small
  inter-chunk pause) so a 3072-module root stays inside wallclock/memory;
* a static-literal metadata fast path: metadata is trusted without any
  subprocess only when the module top level is inert (docstring, Pass,
  allowlisted stdlib imports, plain def/async-def statements) and both
  metadata functions are a single ``return {dict literal}``. Every other
  module falls back to the original isolated, bounded metadata subprocess
  audit, so dynamic overrides keep their legacy rejection behavior;
* lazy source storage: verified bytes are spooled once into a
  content-addressed private directory beside the status database and
  published entries only carry a ``CapturedSource`` reference whose
  ``load_checked()`` re-validates owner, 0600 mode, no symlink/hardlink,
  file size and sha256 on every read.

Owner/hash/schema/context/duplicate/reserved/audit rules are reused from
``discovery`` unchanged. Plugin code is never imported, compiled or
evaluated in the parent process; the parent only parses Python ASTs.
"""

import ast
import copy
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .discovery import (
    CODE_BOUNDS,
    CODE_DATABASE,
    CODE_DIGEST_MISMATCH,
    CODE_DUPLICATE_ID,
    CODE_MANIFEST_INVALID,
    CODE_METADATA_INVALID,
    CODE_MISSING_MANIFEST,
    CODE_MISSING_SKILL,
    CODE_OK,
    CODE_PLUGIN,
    CODE_RESERVED_ID,
    CODE_ROOT,
    CODE_RUNTIME,
    CODE_SYNTAX,
    CODE_SYMLINK,
    _SAFE_NAME,
    _Entry,
    _HEX64,
    _SKILL_ID,
    _static_validate,
    _validate_metadata,
)
from .discovery import SanitizedError

__all__ = ["scan", "CapturedSource", "DEFAULT_CHUNK_SIZE"]

DEFAULT_CHUNK_SIZE = 64
DEFAULT_MAX_PLUGINS = 3072
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MODULE_BYTES = 1_000_000
METADATA_CACHE_BUDGET = 8 * 1024 * 1024
SPOOL_DIRNAME = "discovery-source"
SPOOL_DISK_BUDGET = 128 * 1024 * 1024
_INTER_CHUNK_PAUSE = 0.001
_UNTRUSTED_OWNER = "E_UNTRUSTED_OWNER"
_SCAN_TIMEOUT = "E_SCAN_TIMEOUT"

_CONTRACT_NAMES = frozenset({"Execute", "GetSkillIdentity", "GetContextRequirements"})
_METADATA_FUNCS = ("GetSkillIdentity", "GetContextRequirements")
_FAST_IMPORT_ROOTS = frozenset({
    "asyncio", "base64", "binascii", "bisect", "collections", "datetime",
    "decimal", "difflib", "fractions", "functools", "hashlib", "heapq",
    "itertools", "json", "math", "re", "statistics", "string", "textwrap",
    "time", "unicodedata",
})
_TMP_PREFIX = ".tmp-"


def _read_spooled(path: str, limit: int) -> bytes:
    for parent in [Path(path).parent, *Path(path).parents]:
        if parent.is_symlink():
            raise SanitizedError(CODE_SYMLINK)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise SanitizedError(CODE_DATABASE) from None
    except OSError:
        raise SanitizedError(CODE_SYMLINK) from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SanitizedError(CODE_SYMLINK)
        if info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600:
            raise SanitizedError(_UNTRUSTED_OWNER)
        if info.st_size > limit:
            raise SanitizedError(CODE_BOUNDS)
        blob = handle.read(limit + 1)
        if len(blob) > limit:
            raise SanitizedError(CODE_BOUNDS)
        return blob


@dataclass(frozen=True)
class CapturedSource:
    """Lazy reference to spooled, hash-verified plugin source bytes."""

    path: str
    sha256: str
    size: int = -1

    def load_checked(self, max_bytes: int = MAX_MODULE_BYTES) -> bytes:
        """Load and re-verify spooled bytes; never touches the live module."""
        blob = _read_spooled(self.path, max_bytes)
        if self.size >= 0 and len(blob) != self.size:
            raise SanitizedError(CODE_BOUNDS)
        if hashlib.sha256(blob).hexdigest() != self.sha256:
            raise SanitizedError(CODE_DIGEST_MISMATCH)
        return blob


class _SourceSpool:
    """Content-addressed private store for verified module bytes."""

    def __init__(self, engine):
        base = os.path.dirname(os.path.abspath(engine._database)) or os.getcwd()
        self._root = os.path.join(base, SPOOL_DIRNAME)
        self._ready = False
        self._used = 0

    @property
    def root(self) -> str:
        return self._root

    def _ensure(self) -> None:
        if self._ready:
            return
        try:
            info = os.lstat(self._root)
        except FileNotFoundError:
            for parent in Path(self._root).parents:
                if parent.is_symlink():
                    raise SanitizedError(CODE_SYMLINK)
            try:
                os.mkdir(self._root, 0o700)
            except OSError:
                pass
            try:
                info = os.lstat(self._root)
            except OSError:
                raise SanitizedError(CODE_DATABASE) from None
        except OSError:
            raise SanitizedError(CODE_DATABASE) from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SanitizedError(CODE_SYMLINK)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SanitizedError(_UNTRUSTED_OWNER)
        self._used=0
        with os.scandir(self._root) as walker:
            count=0
            for item in walker:
                count+=1
                if count>10000:raise SanitizedError(CODE_BOUNDS)
                info=item.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):raise SanitizedError(CODE_SYMLINK)
                self._used+=info.st_size
                if self._used>SPOOL_DISK_BUDGET:raise SanitizedError(CODE_BOUNDS)
        self._ready = True

    def store(self, source: bytes, digest: str) -> CapturedSource:
        self._ensure()
        final = os.path.join(self._root, digest)
        replaced_size=0
        try:
            info = os.lstat(final)
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.getuid()
                and info.st_mode & 0o777 == 0o600
            ):
                existing=_read_spooled(final,MAX_MODULE_BYTES)
                if hashlib.sha256(existing).hexdigest()==digest:
                    return CapturedSource(final, digest, len(source))
                # Repair only this store's own regular0600 blob, using verified source.
                replaced_size=info.st_size
            else:
                raise SanitizedError(CODE_SYMLINK)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise SanitizedError(CODE_DATABASE) from error
        if self._used+len(source)>SPOOL_DISK_BUDGET:raise SanitizedError(CODE_BOUNDS)
        tmp = os.path.join(self._root, _TMP_PREFIX+uuid4().hex)
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(source)
            os.chmod(tmp, 0o600)
            os.replace(tmp, final)
        except OSError as error:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise SanitizedError(CODE_DATABASE) from error
        self._used+=len(source)-replaced_size
        return CapturedSource(final, digest, len(source))

    def prune(self, retain: Set[str]) -> None:
        """Delete only own stale hash-named regular files; keep everything else.

        Retention always covers leases plus old/current snapshot and new
        entries, so live disk use stays bounded by two sweep budgets
        (<=64MiB each) which fits inside SPOOL_DISK_BUDGET.
        """
        try:
            names = os.listdir(self._root)
        except OSError:
            return
        for name in names:
            if not _HEX64.fullmatch(name) or name in retain:
                continue
            target = os.path.join(self._root, name)
            try:
                info = os.lstat(target)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode&0o777!=0o600:
                    continue
                os.unlink(target)
                if self._ready:self._used-=info.st_size
            except OSError:
                continue


def _fast_literal_metadata(tree: ast.Module) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return literal metadata, or None when the module must use the child audit.

    Fast path is only eligible when no top-level statement can rebind or
    shadow contract functions before the child reads them, and both metadata
    bodies are a single ``return`` of a dict literal. Values are normalized
    through the same JSON round-trip the metadata child performs so cached,
    static and subprocess results are indistinguishable.
    """
    metadata_defs: Dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Pass):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound in _CONTRACT_NAMES or alias.name not in _FAST_IMPORT_ROOTS:
                    return None
            continue
        if isinstance(node, ast.ImportFrom):
            return None
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            args=node.args
            annotations=[node.returns]+[arg.annotation for arg in args.posonlyargs+args.args+args.kwonlyargs]
            if args.vararg:annotations.append(args.vararg.annotation)
            if args.kwarg:annotations.append(args.kwarg.annotation)
            if getattr(node,'type_params',None):return None
            if node.decorator_list or args.defaults or any(x is not None for x in args.kw_defaults) or any(x is not None for x in annotations):return None
        if isinstance(node, ast.FunctionDef):
            if node.name == "Execute":
                return None
            if node.name in _METADATA_FUNCS:
                metadata_defs[node.name] = node
            continue
        if isinstance(node, ast.AsyncFunctionDef):
            if node.name in ("GetSkillIdentity", "GetContextRequirements"):
                return None
            continue
        return None
    values: List[Dict[str, Any]] = []
    for name in _METADATA_FUNCS:
        node = metadata_defs.get(name)
        if node is None or len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return None
        literal = node.body[0].value
        if not isinstance(literal, ast.Dict) or any(key is None for key in literal.keys):
            return None
        try:
            raw = ast.literal_eval(literal)
            normalized = json.loads(json.dumps(raw, ensure_ascii=True))
        except Exception:
            return None
        if not isinstance(normalized, dict):
            return None
        values.append(normalized)
    return values[0], values[1]


@dataclass
class _ScanCandidate:
    directory: str
    captured: Optional[CapturedSource]
    digest: str
    hash_verified: bool
    identity: Optional[Dict[str, Any]]
    context: Optional[Dict[str, Any]]
    latency_ms: Optional[float]
    source_len: int
    mode: Optional[str]
    code: Optional[str] = None

    def skill_id(self) -> str:
        if isinstance(self.identity, dict):
            value = self.identity.get("id")
            if isinstance(value, str) and _SKILL_ID.match(value):
                return value
        return ""


def _serialized_size(blob: Any) -> int:
    try:
        return len(json.dumps(blob, ensure_ascii=True).encode("utf-8"))
    except Exception:
        return METADATA_CACHE_BUDGET


class _ScanCounters:
    def __init__(self) -> None:
        self.modes = {"static": 0, "cached": 0, "subprocess": 0}
        self._sizes: Dict[str, int] = {}
        self._total = 0

    def baseline(self, engine) -> None:
        for key, blob in list(engine._metadata_cache.items()):
            size = _serialized_size(blob)
            self._sizes[key] = size
            self._total += size

    def bump(self, mode: Optional[str]) -> None:
        if mode in self.modes:
            self.modes[mode] += 1

    def cache_put(self, engine, digest: str, blob: Dict[str, Any]) -> None:
        self._total-=self._sizes.get(digest,0)
        engine._metadata_cache[digest] = copy.deepcopy(blob)
        self._sizes[digest] = _serialized_size(engine._metadata_cache[digest])
        self._total += self._sizes[digest]
        self._evict(engine)

    def _evict(self, engine) -> None:
        if self._total <= METADATA_CACHE_BUDGET:
            return
        while self._total > METADATA_CACHE_BUDGET and engine._metadata_cache:
            oldest = next(iter(engine._metadata_cache))
            del engine._metadata_cache[oldest]
            self._total -= self._sizes.pop(oldest, 0)

    def finish(self, engine, keep: Set[str]) -> int:
        engine._metadata_cache = {k: v for k, v in engine._metadata_cache.items() if k in keep}
        self._sizes = {k: s for k, s in self._sizes.items() if k in engine._metadata_cache}
        self._total = sum(_serialized_size(v) for v in engine._metadata_cache.values())
        self._evict(engine)
        return self._total


def _inspect_lazy(engine, name, path, budget, spool, counters) -> _ScanCandidate:
    digest = ""
    verified = False
    latency: Optional[float] = None
    source_len = 0
    mode: Optional[str] = None
    try:
        source = engine._read_regular(os.path.join(path, "skill.py"), min(engine._max_file_bytes, budget))
        source_len = len(source)
        manifest = engine._read_regular(os.path.join(path, "manifest.json"), 4096)
        pinned = engine._parse_manifest(manifest)
        if pinned is None:
            raise SanitizedError(CODE_MANIFEST_INVALID)
        digest = hashlib.sha256(source).hexdigest()
        if digest != pinned:
            raise SanitizedError(CODE_DIGEST_MISMATCH)
        verified = True
        code = _static_validate(source)
        if code:
            raise SanitizedError(code)
        cached = engine._metadata_cache.get(digest)
        if cached is not None:
            blob = copy.deepcopy(cached)
            latency = 0.0
            mode = "cached"
        else:
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError):
                raise SanitizedError(CODE_SYNTAX) from None
            literal = _fast_literal_metadata(tree)
            if literal is not None:
                blob = {"ok": True, "identity": literal[0], "context": literal[1]}
                latency = 0.0
                mode = "static"
            else:
                started = time.monotonic()
                blob = engine._run_child_sync("metadata", source, None)
                latency = (time.monotonic() - started) * 1000.0
                mode = "subprocess"
        if not isinstance(blob, dict):
            raise SanitizedError(blob if isinstance(blob, str) else CODE_PLUGIN)
        identity = blob.get("identity")
        context = blob.get("context")
        code = _validate_metadata(identity, context)
        if code:
            raise SanitizedError(code)
        counters.cache_put(engine, digest, blob)
        captured = spool.store(source, digest)
        counters.bump(mode)
        return _ScanCandidate(name, captured, digest, True, identity, context, latency, source_len, mode, None)
    except FileNotFoundError as error:
        code = CODE_MISSING_MANIFEST if "manifest.json" in str(error.filename) else CODE_MISSING_SKILL
    except SanitizedError as error:
        code = error.code
    except OSError:
        code = CODE_SYMLINK
    except Exception:
        code = CODE_RUNTIME
    counters.bump(mode)
    return _ScanCandidate(name, None, digest, verified, None, None, latency, source_len, mode, code)


def _retained_hashes(engine, entries: Dict[str, _Entry]) -> Set[str]:
    retain: Set[str] = {entry.descriptor["sha256"] for entry in entries.values()}
    with engine._lock:
        for entry in engine._snapshot.values():
            retain.add(entry.descriptor["sha256"])
    lock = getattr(engine, "_source_lease_lock", None)
    if lock is not None:
        with lock:
            retain.update(getattr(engine, "_source_leases", {}) or {})
    else:
        retain.update(getattr(engine, "_source_leases", {}) or {})
    return retain


def scan(engine, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Dict[str, Any]:
    """Chunked replacement for ``Discovery._scan_sync`` with lazy source storage."""
    chunk_size = max(1, min(int(chunk_size), 1 << 16))
    started = time.monotonic()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    records: List[Tuple] = []
    candidates: List[_ScanCandidate] = []
    counters = _ScanCounters()
    counters.baseline(engine)
    truncated = False
    total_bytes = 0
    names: List[str] = []
    root_error: Optional[str] = None

    def deadline_exceeded() -> bool:
        return time.monotonic() - started > engine._scan_timeout

    try:
        root_stat = os.lstat(engine._root)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            root_error = CODE_SYMLINK
        elif root_stat.st_uid != os.getuid() or root_stat.st_mode & 0o022:
            root_error = _UNTRUSTED_OWNER
    except OSError:
        root_error = CODE_ROOT
    if root_error is None:
        try:
            with os.scandir(engine._root) as walker:
                for item in walker:
                    names.append(item.name)
                    if len(names) > engine._max_plugins:
                        truncated = True
                        root_error = CODE_BOUNDS
                        break
            names = sorted(names[: engine._max_plugins])
        except OSError:
            root_error = CODE_ROOT

    spool = _SourceSpool(engine)
    try:
        spool._ensure()
        spool.prune(_retained_hashes(engine,{}))
    except SanitizedError as error:root_error=error.code
    metadata_total=0
    dirs_seen = 0
    if root_error is None:
        for index in range(0, len(names), chunk_size):
            if deadline_exceeded():
                truncated = True
                root_error = _SCAN_TIMEOUT
                break
            for name in names[index : index + chunk_size]:
                if deadline_exceeded():
                    truncated = True
                    root_error = _SCAN_TIMEOUT
                    break
                if not _SAFE_NAME.fullmatch(name):
                    records.append(engine._record(name, "", None, False, CODE_METADATA_INVALID, None, stamp))
                    continue
                if dirs_seen >= engine._max_plugins:
                    truncated = True
                    break
                path = os.path.join(engine._root, name)
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                if stat.S_ISLNK(info.st_mode):
                    records.append(engine._record(name, "", None, False, CODE_SYMLINK, None, stamp))
                    dirs_seen += 1
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                if info.st_uid != os.getuid() or info.st_mode & 0o022:
                    records.append(engine._record(name, "", None, False, _UNTRUSTED_OWNER, None, stamp))
                    dirs_seen += 1
                    continue
                dirs_seen += 1
                budget = engine._max_total_bytes - total_bytes
                candidate = _inspect_lazy(engine, name, path, budget, spool, counters)
                total_bytes += candidate.source_len
                if candidate.code == CODE_BOUNDS:
                    truncated = True
                if candidate.code is None:
                    metadata_total+=_serialized_size({'identity':candidate.identity,'context':candidate.context})
                    if metadata_total>METADATA_CACHE_BUDGET:
                        truncated=True;root_error=CODE_BOUNDS
                        records.append(engine._record(name,candidate.skill_id(),candidate.digest,True,CODE_BOUNDS,candidate.latency_ms,stamp))
                        break
                    candidates.append(candidate)
                else:
                    records.append(
                        engine._record(
                            name,
                            candidate.skill_id(),
                            candidate.digest,
                            candidate.hash_verified,
                            candidate.code,
                            candidate.latency_ms,
                            stamp,
                        )
                    )
                if truncated:
                    break
            if truncated:
                break
            time.sleep(_INTER_CHUNK_PAUSE)

    grouped: Dict[str, List[_ScanCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.skill_id(), []).append(candidate)
    entries: Dict[str, _Entry] = {}
    for skill_id, group in grouped.items():
        if skill_id in engine._reserved or not engine._identity_policy(skill_id):
            for candidate in group:
                records.append(
                    engine._record(candidate.directory, skill_id, candidate.digest, True, CODE_RESERVED_ID, candidate.latency_ms, stamp)
                )
            continue
        if len(group) > 1:
            for candidate in group:
                records.append(
                    engine._record(candidate.directory, skill_id, candidate.digest, True, CODE_DUPLICATE_ID, candidate.latency_ms, stamp)
                )
            continue
        winner = group[0]
        records.append(
            engine._record(winner.directory, skill_id, winner.digest, True, CODE_OK, winner.latency_ms, stamp)
        )
        entries[skill_id] = _Entry(
            descriptor={
                "id": skill_id,
                "status": "available",
                "engine": "discovery",
                "description": winner.identity["name"],
                "directly_invocable": True,
                "execution_target": "operator_managed_pure_plugin",
                "hash_verified": True,
                "signature_verified": False,
                "name": winner.identity["name"],
                "core_capabilities": list(winner.identity["core_capabilities"]),
                "requirements": list(winner.identity["requirements"]),
                "input_schema": dict(winner.identity["input_schema"]),
                "context": dict(winner.context),
                "sha256": winner.digest,
                "verified_at": stamp,
            },
            source=winner.captured,
        )

    codes: Dict[str, int] = {}
    rejected = 0
    for row in records:
        code = row[5]
        if code != CODE_OK:
            rejected += 1
            codes[code] = codes.get(code, 0) + 1
    summary = engine._default_summary()
    summary["finished_at"] = stamp
    summary["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
    summary["scanned_dirs"] = dirs_seen
    summary["valid"] = len(entries)
    summary["rejected"] = rejected
    summary["registered"] = len(entries)
    summary["skill_ids"] = sorted(entries)
    summary["error_codes"] = codes
    summary["truncated"] = truncated
    summary["result"] = CODE_OK if root_error is None else root_error
    summary["scanning"] = False
    summary["chunk_size"] = chunk_size
    summary["lazy_source_storage"] = True
    summary["source_bytes"] = total_bytes
    summary["snapshot_metadata_bytes"] = metadata_total
    summary["metadata_static"] = counters.modes["static"]
    summary["metadata_cached"] = counters.modes["cached"]
    summary["metadata_subprocess"] = counters.modes["subprocess"]
    if root_error is not None or truncated:
        summary["result"] = root_error or CODE_BOUNDS
        records = [row[:5] + ("E_SCAN_INCOMPLETE",) + row[6:] if row[5] == CODE_OK else row for row in records]
        summary["rejected"] = sum(row[5] != CODE_OK for row in records)
        codes = {}
        for row in records:
            if row[5] != CODE_OK:
                codes[row[5]] = codes.get(row[5], 0) + 1
        summary["error_codes"] = codes
        entries = {}
        summary["registered"] = summary["valid"] = 0
        summary["skill_ids"] = []
    summary["status"] = "degraded" if root_error or rejected or truncated else "ready"
    engine._generation += 1
    summary["generation"] = engine._generation
    summary["db_result"] = engine._persist(records, summary)
    if summary["db_result"] != CODE_OK:
        entries = {}
        summary["registered"] = summary["valid"] = 0
        summary["skill_ids"] = []
        summary["status"] = "degraded"
    hashes = {entry.descriptor["sha256"] for entry in entries.values()}
    summary["metadata_bytes"] = counters.finish(engine, hashes)
    spool.prune(_retained_hashes(engine, entries))
    return {"entries": entries, "summary": summary}
