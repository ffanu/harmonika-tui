"""Technical-only skill failure log: schema, hygiene, retention, wiring, isolation."""
import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.engine_registry.discovery import Discovery, SanitizedError
from backend.engine_registry.failure_log import ALLOWED_ERROR_TYPES, FailureLog

VALID_TEMPLATE = '''import asyncio

async def Execute(payload):
    return {"marker": "MARKER_VALUE"}

def GetSkillIdentity():
    return {"id": "SKILL_ID", "name": "NAME", "core_capabilities": ["cap"], "requirements": [], "input_schema": {"type": "object", "additionalProperties": True}}

def GetContextRequirements():
    return {"max_input_tokens": 65536, "max_output_tokens": 4096, "max_chat_messages": 0, "max_tool_calls": 0}
'''

STRICT_SCHEMA = '"input_schema": {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 12000}}, "required": ["text"], "additionalProperties": False}'
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def make_source(skill_id, marker="ok"):
    return VALID_TEMPLATE.replace("MARKER_VALUE", marker).replace("SKILL_ID", skill_id).replace("NAME", "Skill")


def boom_source():
    return make_source("test.boom").replace('return {"marker": "ok"}', 'raise RuntimeError("X_EXC_SENTINEL")')


def badout_source():
    return make_source("test.badout").replace('return {"marker": "ok"}', 'return [1, 2, 3]')


def slow_source():
    return make_source("test.slow").replace('return {"marker": "ok"}', 'await asyncio.sleep(60)\n    return {}')


def strict_source():
    return make_source("test.strict").replace('"input_schema": {"type": "object", "additionalProperties": True}', STRICT_SCHEMA)


def stamp(offset_seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_seconds))


class FailureLogCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.db = self.base / "failures.db"

    def connect(self):
        return contextlib.closing(sqlite3.connect(self.db))

    def rows(self):
        with self.connect() as connection:
            return connection.execute("SELECT timestamp, skill_id, error_type, latency FROM skill_failures ORDER BY rowid").fetchall()

    def count(self):
        with self.connect() as connection:
            return connection.execute("SELECT count(*) FROM skill_failures").fetchone()[0]

    def storage_bytes(self):
        return b"\n".join(path.read_bytes() for path in sorted(self.base.glob("failures.db*")))

    def test_schema_is_exactly_four_technical_columns(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("test.skill", "skill_exception", 1.25), "ok")
        with self.connect() as connection:
            info = connection.execute("PRAGMA table_info(skill_failures)").fetchall()
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            indexes = connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'").fetchall()
        self.assertEqual(len(info), 4)
        self.assertEqual([row[1] for row in info], ["timestamp", "skill_id", "error_type", "latency"])
        self.assertEqual([row[2] for row in info], ["TEXT", "TEXT", "TEXT", "REAL"])
        self.assertEqual([row[5] for row in info], [0, 0, 0, 0])
        self.assertEqual(tables, [("skill_failures",)])
        self.assertEqual(indexes, [])
        self.assertEqual(stat.S_IMODE(os.stat(self.db).st_mode), 0o600)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        timestamp, skill_id, error_type, latency = rows[0]
        self.assertTrue(TIMESTAMP_RE.match(timestamp))
        self.assertEqual((skill_id, error_type, latency), ("test.skill", "skill_exception", 1.25))

    def test_error_type_allowlist_and_unknown_mapping(self):
        log = FailureLog(self.db)
        for error_type in sorted(ALLOWED_ERROR_TYPES):
            self.assertEqual(log.record("test.skill", error_type, 1.0), "ok")
        free_form = "X_PII_TOKEN_SENTINEL someone@X_PII_EMAIL_SENTINEL /Users/X_PII_PATH_SENTINEL"
        self.assertEqual(log.record("test.skill", free_form, 1.0), "ok")
        self.assertEqual(log.record("test.skill", None, 1.0), "ok")
        self.assertEqual(self.count(), len(ALLOWED_ERROR_TYPES) + 2)
        with self.connect() as connection:
            stored = {row[0] for row in connection.execute("SELECT DISTINCT error_type FROM skill_failures")}
        self.assertEqual(stored, set(ALLOWED_ERROR_TYPES))
        blob = self.storage_bytes()
        for sentinel in (b"X_PII_TOKEN_SENTINEL", b"X_PII_EMAIL_SENTINEL", b"X_PII_PATH_SENTINEL"):
            self.assertNotIn(sentinel, blob)

    def test_skill_id_and_latency_validation(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
        for bad_id in ["Bad.ID", "x" * 101, "drop table skill_failures", "", "a b", "skill\nid", None, 123, "-bad", "test.skill extra"]:
            self.assertEqual(log.record(bad_id, "skill_exception", 1.0), "ok")
        self.assertEqual(self.count(), 1)
        for bad_latency in [-1.0, float("nan"), float("inf"), "fast", None, object()]:
            self.assertEqual(log.record("test.skill", "skill_exception", bad_latency), "ok")
        self.assertEqual(self.count(), 1)
        self.assertEqual(log.record("test.skill", "skill_exception", 0), "ok")
        self.assertEqual(log.record("test.skill", "skill_timeout", 7), "ok")
        self.assertEqual([row[3] for row in self.rows()], [1.0, 0.0, 7.0])

    def test_summary_shape_and_limits(self):
        log = FailureLog(self.db)
        events = [
            ("alpha.skill", "skill_timeout", 10.0),
            ("alpha.skill", "skill_exception", 20.0),
            ("beta.skill", "invalid_input", 30.0),
            ("beta.skill", "invalid_output", 40.0),
            ("alpha.skill", "cancelled", 50.0),
            ("beta.skill", "graphics_offline", 60.0),
            ("alpha.skill", "logging_unavailable", 70.0),
        ]
        for skill_id, error_type, latency in events:
            self.assertEqual(log.record(skill_id, error_type, latency), "ok")
        summary = log.summary()
        self.assertEqual(set(summary), {"logging", "count", "recent"})
        self.assertEqual(summary["logging"], "ok")
        self.assertEqual(summary["count"], 7)
        self.assertEqual(len(summary["recent"]), 5)
        self.assertEqual(summary["recent"][0]["error_type"], "logging_unavailable")
        self.assertEqual(summary["recent"][0]["latency"], 70.0)
        for item in summary["recent"]:
            self.assertEqual(set(item), {"timestamp", "skill_id", "error_type", "latency"})
            self.assertTrue(TIMESTAMP_RE.match(item["timestamp"]))
        self.assertEqual([item["error_type"] for item in log.summary(limit=3)["recent"]], ["logging_unavailable", "graphics_offline", "cancelled"])
        self.assertEqual(len(log.summary(limit=0)["recent"]), 1)
        self.assertEqual(len(log.summary(limit=-3)["recent"]), 1)
        self.assertEqual(len(log.summary(limit="many")["recent"]), 5)

    def test_retention_keeps_latest_thousand_rows(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("warm.skill", "skill_exception", 1.0), "ok")
        seeded = [stamp(-3600 + index) for index in range(1000)]
        with self.connect() as connection:
            connection.execute("DELETE FROM skill_failures")
            connection.executemany(
                "INSERT INTO skill_failures (timestamp, skill_id, error_type, latency) VALUES (?, ?, ?, ?)",
                [(value, "seed.skill", "skill_exception", 1.0) for value in seeded],
            )
            connection.commit()
        for index in range(5):
            self.assertEqual(log.record("test.skill", "skill_timeout", float(index)), "ok")
        with self.connect() as connection:
            stored = [row[0] for row in connection.execute("SELECT timestamp FROM skill_failures")]
        self.assertEqual(len(stored), 1000)
        self.assertTrue(set(seeded[:5]).isdisjoint(stored))
        self.assertTrue(set(seeded[5:]).issubset(stored))
        self.assertEqual(len([value for value in stored if value not in set(seeded)]), 5)

    def test_retention_drops_rows_older_than_seven_days(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("warm.skill", "skill_exception", 1.0), "ok")
        stale = stamp(-8 * 86400 - 100)
        fresh = stamp(-6 * 86400)
        with self.connect() as connection:
            connection.execute("DELETE FROM skill_failures")
            connection.executemany(
                "INSERT INTO skill_failures (timestamp, skill_id, error_type, latency) VALUES (?, ?, ?, ?)",
                [(stale, "old.skill", "skill_exception", 1.0)] * 3 + [(fresh, "new.skill", "skill_timeout", 2.0)] * 2,
            )
            connection.commit()
        self.assertEqual(log.record("test.skill", "cancelled", 3.0), "ok")
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        timestamps = [row[0] for row in rows]
        self.assertNotIn(stale, timestamps)
        self.assertEqual(timestamps.count(fresh), 2)
        self.assertEqual(sorted(row[1] for row in rows), ["new.skill", "new.skill", "test.skill"])

    def test_busy_database_returns_logging_unavailable(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
        holder = sqlite3.connect(self.db, isolation_level=None, timeout=0.1)
        try:
            holder.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            self.assertEqual(log.record("test.skill", "skill_exception", 2.0), "logging_unavailable")
            self.assertLess(time.monotonic() - started, 10.0)
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(log.record("test.skill", "skill_exception", 3.0), "ok")
        self.assertEqual(self.count(), 2)

    def test_hardened_database_paths(self):
        with self.subTest(case="symlink"):
            target = self.base / "target.db"
            target.write_bytes(b"preserve-me")
            link = self.base / "link.db"
            link.symlink_to(target)
            self.assertEqual(FailureLog(link).record("test.skill", "skill_exception", 1.0), "logging_unavailable")
            self.assertEqual(target.read_bytes(), b"preserve-me")
        with self.subTest(case="symlink ancestor"):
            real = self.base / "real"
            real.mkdir()
            alias = self.base / "alias"
            alias.symlink_to(real, target_is_directory=True)
            self.assertEqual(FailureLog(alias / "failures.db").record("test.skill", "skill_exception", 1.0), "logging_unavailable")
            self.assertEqual(list(real.iterdir()), [])
        with self.subTest(case="wrong mode"):
            log = FailureLog(self.db)
            self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
            os.chmod(self.db, 0o644)
            self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "logging_unavailable")
            os.chmod(self.db, 0o600)
            self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
        with self.subTest(case="hardlink"):
            extra = self.base / "extra.db"
            os.link(self.db, extra)
            self.assertEqual(FailureLog(self.db).record("test.skill", "skill_exception", 1.0), "logging_unavailable")
            extra.unlink()
            self.assertEqual(FailureLog(self.db).record("test.skill", "skill_exception", 1.0), "ok")
        with self.subTest(case="replaced file"):
            log = FailureLog(self.db)
            self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
            self.db.rename(self.base / "old.db")
            replacement = sqlite3.connect(self.db)
            replacement.execute("CREATE TABLE other(value TEXT)")
            replacement.commit()
            replacement.close()
            os.chmod(self.db, 0o600)
            before = self.db.read_bytes()
            self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "logging_unavailable")
            self.assertEqual(self.db.read_bytes(), before)
        with self.subTest(case="directory path"):
            self.assertEqual(FailureLog(self.base).record("test.skill", "skill_exception", 1.0), "logging_unavailable")
            self.assertEqual(FailureLog(self.base).summary()["logging"], "logging_unavailable")

    def test_deleted_database_is_not_recreated(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
        self.db.unlink()
        self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "logging_unavailable")
        self.assertFalse(self.db.exists())
        self.assertEqual(log.summary(), {"logging": "logging_unavailable", "count": 0, "recent": []})

    def test_missing_database_created_on_first_record(self):
        missing = self.base / "missing.db"
        log = FailureLog(missing)
        self.assertEqual(log.summary(), {"logging": "logging_unavailable", "count": 0, "recent": []})
        self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
        self.assertTrue(missing.exists())
        self.assertEqual(stat.S_IMODE(os.stat(missing).st_mode), 0o600)
        self.assertEqual(log.summary()["count"], 1)

    def test_journal_mode_never_reset(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("test.skill", "skill_exception", 1.0), "ok")
        with self.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=wal").fetchone()[0], "wal")
        fresh = FailureLog(self.db)
        self.assertEqual(fresh.record("test.skill", "skill_timeout", 2.0), "ok")
        self.assertEqual(fresh.summary()["count"], 2)
        with self.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_concurrent_records_are_safe(self):
        log = FailureLog(self.db)
        self.assertEqual(log.record("warm.skill", "skill_exception", 1.0), "ok")
        with self.connect() as connection:
            connection.execute("DELETE FROM skill_failures")
            connection.commit()
        statuses = []
        summaries = []
        stop = threading.Event()

        def writer():
            for index in range(10):
                statuses.append(log.record("test.skill", "skill_exception", float(index)))

        def reader():
            while not stop.is_set():
                summaries.append(log.summary(limit=3))
                time.sleep(0.005)

        threads = [threading.Thread(target=writer) for _ in range(8)]
        reader_thread = threading.Thread(target=reader)
        reader_thread.start()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        stop.set()
        reader_thread.join()
        self.assertEqual(len(statuses), 80)
        self.assertTrue(all(status in ("ok", "logging_unavailable") for status in statuses))
        recorded = statuses.count("ok")
        self.assertEqual(self.count(), recorded)
        with self.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        summary = log.summary()
        self.assertEqual(summary["logging"], "ok")
        self.assertEqual(summary["count"], recorded)
        self.assertEqual(len(summary["recent"]), 5)
        self.assertTrue(all(isinstance(item, dict) for item in summaries))


class DiscoveryWiringCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "plugins"
        self.root.mkdir()
        self.db = self.base / "status.db"

    def plugin(self, name, source):
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        raw = source.encode("utf-8")
        (directory / "skill.py").write_bytes(raw)
        (directory / "manifest.json").write_text(json.dumps({"module_sha256": hashlib.sha256(raw).hexdigest()}))
        return directory

    def failure_rows(self):
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            return connection.execute("SELECT timestamp, skill_id, error_type, latency FROM skill_failures ORDER BY rowid").fetchall()

    def failure_count(self):
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            return connection.execute("SELECT count(*) FROM skill_failures").fetchone()[0]

    def execution_rows(self):
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            return connection.execute("SELECT skill_id, error_code FROM engine_discovery_execution").fetchall()

    def storage_bytes(self):
        return b"\n".join(path.read_bytes() for path in sorted(self.base.glob("status.db*")))

    async def test_plugin_exception_recorded_without_leaks(self):
        self.plugin("boomer", boom_source())
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        logging.getLogger().addHandler(handler)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SanitizedError) as caught:
                    await engine.execute("test.boom", {"secret": "X_PAYLOAD_SENTINEL", "note": "X_NOTE_SENTINEL"})
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertEqual(caught.exception.code, "E_PLUGIN")
        rows = self.failure_rows()
        self.assertEqual(len(rows), 1)
        timestamp, skill_id, error_type, latency = rows[0]
        self.assertTrue(TIMESTAMP_RE.match(timestamp))
        self.assertEqual(skill_id, "test.boom")
        self.assertEqual(error_type, "skill_exception")
        self.assertIsInstance(latency, float)
        self.assertGreaterEqual(latency, 0.0)
        self.assertEqual(self.execution_rows(), [("test.boom", "E_PLUGIN")])
        blob = self.storage_bytes()
        for sentinel in (b"X_EXC_SENTINEL", b"X_PAYLOAD_SENTINEL", b"X_NOTE_SENTINEL", b"Traceback", b"RuntimeError"):
            self.assertNotIn(sentinel, blob)
        for text in (out.getvalue(), err.getvalue(), "\n".join(records)):
            for sentinel in ("X_EXC_SENTINEL", "X_PAYLOAD_SENTINEL", "X_NOTE_SENTINEL"):
                self.assertNotIn(sentinel, text)

    async def test_invalid_input_recorded_without_execution_audit(self):
        self.plugin("strict", strict_source())
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        with self.assertRaises(SanitizedError) as first:
            await engine.execute("test.strict", {"text": "x" * 66000})
        self.assertEqual(first.exception.code, "E_PAYLOAD")
        with self.assertRaises(SanitizedError) as second:
            await engine.execute("test.strict", {"bad": object()})
        self.assertEqual(second.exception.code, "E_PAYLOAD")
        rows = self.failure_rows()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row[1], "test.strict")
            self.assertEqual(row[2], "invalid_input")
            self.assertGreaterEqual(row[3], 0.0)
        self.assertEqual(self.execution_rows(), [])

    async def test_timeout_and_bad_output_recorded(self):
        self.plugin("slow", slow_source())
        self.plugin("badout", badout_source())
        engine = Discovery(self.root, self.db, execute_timeout=0.5)
        self.addCleanup(engine.close)
        await engine.refresh()
        with self.assertRaises(SanitizedError) as timed_out:
            await engine.execute("test.slow", {})
        self.assertEqual(timed_out.exception.code, "E_TIMEOUT")
        with self.assertRaises(SanitizedError) as bad:
            await engine.execute("test.badout", {})
        self.assertEqual(bad.exception.code, "E_OUTPUT")
        self.assertEqual(
            sorted((row[1], row[2]) for row in self.failure_rows()),
            [("test.badout", "invalid_output"), ("test.slow", "skill_timeout")],
        )

    async def test_unknown_skill_records_no_event(self):
        self.plugin("good", make_source("test.good"))
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        with self.assertRaises(SanitizedError) as missing:
            await engine.execute("missing.skill", {})
        self.assertEqual(missing.exception.code, "E_NOT_REGISTERED")
        with self.assertRaises(SanitizedError) as unregistered:
            await engine.execute("other.skill", {"any": True})
        self.assertEqual(unregistered.exception.code, "E_NOT_REGISTERED")
        self.assertEqual(self.failure_count(), 0)
        self.assertEqual(engine._failure_log.summary(), {"logging": "ok", "count": 0, "recent": []})

    async def test_cancellation_recorded_and_propagates(self):
        self.plugin("slow", slow_source())
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        task = asyncio.create_task(engine.execute("test.slow", {"secret": "X_CANCEL_SENTINEL"}))
        await asyncio.sleep(0.3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        rows = self.failure_rows()
        self.assertEqual([(row[1], row[2]) for row in rows], [("test.slow", "cancelled")])
        self.assertEqual(self.execution_rows(), [("test.slow", "E_CANCELLED")])
        self.assertNotIn(b"X_CANCEL_SENTINEL", self.storage_bytes())

    async def test_successful_execution_records_no_failure(self):
        self.plugin("good", make_source("test.good"))
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        self.assertEqual(await engine.execute("test.good", {"value": 1}), {"marker": "ok"})
        self.assertEqual(self.failure_count(), 0)
        self.assertEqual(self.execution_rows(), [("test.good", "OK")])

    async def test_busy_database_does_not_change_skill_execution(self):
        self.plugin("boomer", boom_source())
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        holder = sqlite3.connect(self.db, isolation_level=None, timeout=0.1)
        try:
            holder.execute("BEGIN IMMEDIATE")
            with self.assertLogs("harmonika.discovery.failure", level="WARNING") as logged:
                with self.assertRaises(SanitizedError) as caught:
                    await asyncio.wait_for(engine.execute("test.boom", {"secret": "X_BUSY_SENTINEL"}), 20)
            self.assertEqual(caught.exception.code, "E_PLUGIN")
            text = "\n".join(logged.output)
            self.assertIn("failure_log_unavailable", text)
            self.assertNotIn("X_BUSY_SENTINEL", text)
            self.assertEqual(self.failure_count(), 0)
        finally:
            holder.rollback()
            holder.close()
        with self.assertRaises(SanitizedError) as replay:
            await engine.execute("test.boom", {"secret": "X_BUSY_SENTINEL"})
        self.assertEqual(replay.exception.code, "E_PLUGIN")
        self.assertEqual(self.failure_count(), 1)
        self.assertEqual(self.failure_rows()[0][2], "skill_exception")
        self.assertNotIn(b"X_BUSY_SENTINEL", self.storage_bytes())

    async def test_schema_created_by_registry_and_strict_allowlist_preserved(self):
        self.plugin("good", make_source("test.good"))
        engine = Discovery(self.root, self.db)
        await engine.refresh()
        engine.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            info = connection.execute("PRAGMA table_info(skill_failures)").fetchall()
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(len(info), 4)
        self.assertEqual([row[1] for row in info], ["timestamp", "skill_id", "error_type", "latency"])
        self.assertEqual([row[2] for row in info], ["TEXT", "TEXT", "TEXT", "REAL"])
        self.assertEqual(tables, {"engine_discovery_status", "engine_discovery_summary", "engine_discovery_execution", "skill_failures"})
        revived = Discovery(self.root, self.db)
        await revived.refresh()
        revived.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            connection.execute("CREATE TABLE rogue(value TEXT)")
            connection.commit()
        with self.assertRaises(SanitizedError):
            Discovery(self.root, self.db)

    async def test_wal_registry_database_is_preserved(self):
        self.plugin("boomer", boom_source())
        engine = Discovery(self.root, self.db)
        await engine.refresh()
        engine.close()
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=wal").fetchone()[0], "wal")
        engine = Discovery(self.root, self.db)
        self.addCleanup(engine.close)
        await engine.refresh()
        with self.assertRaises(SanitizedError) as caught:
            await engine.execute("test.boom", {})
        self.assertEqual(caught.exception.code, "E_PLUGIN")
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        rows = self.failure_rows()
        self.assertEqual([(row[1], row[2]) for row in rows], [("test.boom", "skill_exception")])


if __name__ == "__main__":
    unittest.main()
