"""Technical-only failure log for registered engine skills.

Stores mechanical fields only (timestamp, skill_id, error_type, latency in
milliseconds) in a native SQLite ``skill_failures`` table. Never stores
identity, prompts, payloads, messages, URLs, paths, tokens or payload hashes.
Every operation is fail-safe: record/summary return a mechanical status
instead of raising or printing raw exception output, and journal/WAL state of
the shared registry database is never reset or migrated here.
"""

import math
import os
import re
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

__all__ = [
    "FailureLog",
    "TABLE_DDL",
    "ALLOWED_ERROR_TYPES",
    "DEFAULT_ERROR_TYPE",
    "LOGGING_UNAVAILABLE",
    "STATUS_OK",
]

STATUS_OK = "ok"
LOGGING_UNAVAILABLE = "logging_unavailable"
DEFAULT_ERROR_TYPE = "skill_exception"
DEFAULT_SUMMARY_LIMIT = 5

ALLOWED_ERROR_TYPES = frozenset({
    "skill_exception",
    "skill_timeout",
    "invalid_input",
    "invalid_output",
    "cancelled",
    "graphics_offline",
    "graphics_busy",
    "graphics_vram_full",
    "graphics_timeout",
    "graphics_bad_payload",
    "graphics_auth_failed",
    "graphics_warming",
    "graphics_recovery_required",
    "graphics_failed",
    "logging_unavailable",
})

RETENTION_MAX_ROWS = 1000
RETENTION_MAX_AGE_DAYS = 7
MAX_SKILL_ID_LENGTH = 100
DEFAULT_BUSY_TIMEOUT_MS = 500
MAX_BUSY_TIMEOUT_MS = 2000
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS skill_failures ("
    "timestamp TEXT NOT NULL, "
    "skill_id TEXT NOT NULL, "
    "error_type TEXT NOT NULL, "
    "latency REAL NOT NULL)"
)

_SKILL_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


def _valid_skill_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_SKILL_ID_LENGTH
        and _SKILL_ID.fullmatch(value) is not None
    )


def _coerce_latency(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:number = float(value)
    except (OverflowError,ValueError,TypeError):return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number, 3)


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(value, high))


def _utc_timestamp(when: Optional[float] = None) -> str:
    return time.strftime(TIMESTAMP_FORMAT, time.gmtime(when))


class FailureLog:
    """Fail-safe, technical-only ``skill_failures`` writer/reader.

    The database path is hardened per use: no symlinks anywhere in the path
    chain, regular file with a single hardlink, owned by the current user and
    without group/other permissions. File identity (device, inode) is pinned
    on first successful open so a replaced database never receives records.
    All SQLite access uses a short bounded busy timeout and never changes the
    journal mode or WAL retention of the shared registry database.
    """

    def __init__(
        self,
        database_path: Union[str, os.PathLike],
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        max_rows: int = RETENTION_MAX_ROWS,
        max_age_days: int = RETENTION_MAX_AGE_DAYS,
    ):
        self._database = os.path.abspath(os.fspath(database_path))
        self._busy_timeout_ms = _bounded_int(busy_timeout_ms, 1, MAX_BUSY_TIMEOUT_MS, DEFAULT_BUSY_TIMEOUT_MS)
        self._max_rows = _bounded_int(max_rows, 1, 1_000_000, RETENTION_MAX_ROWS)
        self._max_age_days = _bounded_int(max_age_days, 1, 3650, RETENTION_MAX_AGE_DAYS)
        self._identity: Optional[Tuple[int, int]] = None
        self._probe_identity()

    def record(self, skill_id: Any, error_type: Any, latency: Any) -> str:
        """Append one mechanical failure event; never raises.

        Unknown free-form error strings are mapped to the fixed
        ``skill_exception`` type. Non-canonical skill ids and non-finite
        latencies are dropped without writing. Returns ``STATUS_OK`` or
        ``LOGGING_UNAVAILABLE``; failures never affect skill execution.
        """
        if not _valid_skill_id(skill_id):
            return STATUS_OK
        resolved = (
            error_type
            if isinstance(error_type, str) and error_type in ALLOWED_ERROR_TYPES
            else DEFAULT_ERROR_TYPE
        )
        latency_ms = _coerce_latency(latency)
        if latency_ms is None:
            return STATUS_OK
        connection = self._connect(create=True)
        if connection is None:
            return LOGGING_UNAVAILABLE
        try:
            with connection:
                connection.execute(TABLE_DDL)
                connection.execute(
                    "INSERT INTO skill_failures (timestamp, skill_id, error_type, latency)"
                    " VALUES (?, ?, ?, ?)",
                    (_utc_timestamp(), skill_id, resolved, latency_ms),
                )
                connection.execute(
                    "DELETE FROM skill_failures WHERE timestamp < ?",
                    (_utc_timestamp(time.time() - self._max_age_days * 86400),),
                )
                connection.execute(
                    "DELETE FROM skill_failures WHERE rowid NOT IN"
                    " (SELECT rowid FROM skill_failures ORDER BY timestamp DESC, rowid DESC LIMIT ?)",
                    (self._max_rows,),
                )
            return STATUS_OK
        except (sqlite3.Error, OSError, ValueError):
            return LOGGING_UNAVAILABLE
        finally:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def summary(self, limit: int = DEFAULT_SUMMARY_LIMIT) -> Dict[str, Any]:
        """Return the latest mechanical events, a count and logging status."""
        bounded = _bounded_int(limit, 1, 100, DEFAULT_SUMMARY_LIMIT)
        connection = self._connect(create=False)
        if connection is None:
            return {"logging": LOGGING_UNAVAILABLE, "count": 0, "recent": []}
        try:
            present = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='skill_failures'"
            ).fetchone()
            if present is None:
                return {"logging": STATUS_OK, "count": 0, "recent": []}
            rows = connection.execute(
                "SELECT timestamp, skill_id, error_type, latency FROM skill_failures"
                " ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            count = connection.execute("SELECT count(*) FROM skill_failures").fetchone()[0]
            return {
                "logging": STATUS_OK,
                "count": int(count),
                "recent": [
                    {
                        "timestamp": row[0],
                        "skill_id": row[1],
                        "error_type": row[2],
                        "latency": float(row[3]),
                    }
                    for row in rows
                ],
            }
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return {"logging": LOGGING_UNAVAILABLE, "count": 0, "recent": []}
        finally:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _probe_identity(self) -> None:
        try:
            info = os.lstat(self._database)
            if self._file_ok(info):
                self._identity = (info.st_dev, info.st_ino)
        except OSError:
            self._identity = None

    def _file_ok(self, info: os.stat_result) -> bool:
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077
        )

    def _connect(self, *, create: bool):
        connection = None
        try:
            path = Path(self._database)
            for item in (path, *path.parents):
                if item.is_symlink():
                    return None
            try:
                info = os.lstat(self._database)
            except FileNotFoundError:
                if not create or self._identity is not None:
                    return None
                directory = os.path.dirname(self._database)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                fd = os.open(self._database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                try:
                    info = os.lstat(self._database)
                except OSError:
                    return None
            except OSError:
                return None
            if not self._file_ok(info):
                return None
            identity = (info.st_dev, info.st_ino)
            if self._identity is None:
                self._identity = identity
            elif self._identity != identity:
                return None
            for suffix in ('-wal','-shm','-journal'):
                try:
                    sidecar=os.lstat(self._database+suffix)
                except FileNotFoundError:
                    continue
                if not self._file_ok(sidecar):return None
            connection = sqlite3.connect(
                Path(self._database).as_uri() + "?mode=rw",
                uri=True,
                timeout=self._busy_timeout_ms / 1000.0,
            )
            connection.execute("PRAGMA busy_timeout=%d" % self._busy_timeout_ms)
            schema=connection.execute("PRAGMA table_info(skill_failures)").fetchall()
            expected=[('timestamp','TEXT'),('skill_id','TEXT'),('error_type','TEXT'),('latency','REAL')]
            if schema and [(row[1],row[2].upper()) for row in schema]!=expected:
                connection.close();return None
            return connection
        except (OSError, sqlite3.Error, ValueError):
            if connection is not None:
                try:connection.close()
                except sqlite3.Error:pass
            return None
