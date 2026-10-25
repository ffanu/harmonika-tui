import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.engine_registry import discovery

VALID_TEMPLATE = '''import asyncio

async def Execute(payload):
    return {"marker": "MARKER_VALUE"}

def GetSkillIdentity():
    return {"id": "SKILL_ID", "name": "NAME", "core_capabilities": ["cap"], "requirements": [], "input_schema": {"type": "object", "additionalProperties": True}}

def GetContextRequirements():
    return {"max_input_tokens": 65536, "max_output_tokens": 4096, "max_chat_messages": 0, "max_tool_calls": 0}
'''


def make_source(skill_id, marker="ok", name="Skill"):
    return VALID_TEMPLATE.replace("MARKER_VALUE", marker).replace("SKILL_ID", skill_id).replace("NAME", name)


class DiscoveryCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "skills"
        self.root.mkdir()
        self.db = base / "status.sqlite3"
        self.session_db = base / "session.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def make_plugin(self, dirname, source=None, sha=None, manifest=None, skill_id="test.skill", marker="ok"):
        directory = self.root / dirname
        directory.mkdir(parents=True, exist_ok=True)
        raw = (source if source is not None else make_source(skill_id, marker)).encode("utf-8")
        (directory / "skill.py").write_bytes(raw)
        if manifest is None:
            digest = sha if sha is not None else hashlib.sha256(raw).hexdigest()
            manifest = {"module_sha256": digest}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return directory

    def rows(self):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(
                "SELECT root, directory, skill_id, sha256, hash_verified, error_code, audit_latency_ms, updated_at"
                " FROM engine_discovery_status"
            ).fetchall()
        finally:
            connection.close()

    def code_for(self, dirname):
        return sorted(row[5] for row in self.rows() if row[1] == dirname)

    def dump_db_text(self):
        connection = sqlite3.connect(self.db)
        try:
            chunks = []
            for table in ("engine_discovery_status", "engine_discovery_summary", "engine_discovery_execution"):
                try:
                    chunks.extend(str(row) for row in connection.execute("SELECT * FROM " + table))
                except sqlite3.Error:
                    pass
        finally:
            connection.close()
        return "\n".join(chunks)

    async def test_valid_plugin_registers_and_executes(self):
        self.make_plugin("doubler", skill_id="test.doubler", marker="one")
        engine = discovery.Discovery(self.root, self.db)
        summary = await engine.refresh()
        self.assertEqual(summary["result"], "OK")
        self.assertEqual(summary["valid"], 1)
        descriptors = engine.descriptors()
        self.assertEqual(len(descriptors), 1)
        descriptor = descriptors[0]
        self.assertEqual(descriptor["id"], "test.doubler")
        self.assertTrue(descriptor['hash_verified']);self.assertFalse(descriptor['signature_verified'])
        for key in ("name", "core_capabilities", "requirements", "input_schema", "context", "sha256", "verified_at"):
            self.assertIn(key, descriptor)
        self.assertNotIn("source", descriptor)
        result = await engine.execute("test.doubler", {"value": 21})
        self.assertEqual(result, {"marker": "one"})
        row = [row for row in self.rows() if row[1] == "doubler"][0]
        self.assertEqual(row[5], "OK")
        self.assertEqual(row[4], 1)
        self.assertIsNotNone(row[6])
        engine.close()

    async def test_broken_syntax_rejected(self):
        self.make_plugin("broken", source="def (:\n")
        engine = discovery.Discovery(self.root, self.db)
        summary = await engine.refresh()
        self.assertEqual(summary["valid"], 0)
        self.assertEqual(self.code_for("broken"), ["E_SYNTAX"])
        engine.close()

    async def test_missing_signature_rejected(self):
        source = (
            "async def Execute(payload):\n    return {}\n\n"
            "def GetSkillIdentity():\n"
            '    return {"id": "x.y", "name": "X", "core_capabilities": [], "requirements": [], "input_schema": {}}\n'
        )
        self.make_plugin("nosig", source=source)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("nosig"), ["E_CONTRACT"])
        self.assertEqual(engine.descriptors(), [])
        engine.close()

    async def test_sync_execute_rejected(self):
        source = VALID_TEMPLATE.replace("async def Execute", "def Execute").replace("MARKER_VALUE", "x").replace("SKILL_ID", "a.b").replace("NAME", "S")
        self.make_plugin("syncexec", source=source)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("syncexec"), ["E_CONTRACT"])
        engine.close()

    async def test_toplevel_raise_rejected(self):
        source = make_source("raise.one") + "\nraise RuntimeError('X_EXC_SENTINEL')\n"
        self.make_plugin("raiser", source=source)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("raiser"), ["E_CONTRACT_STATIC"])
        self.assertEqual(engine.descriptors(), [])
        engine.close()

    async def test_toplevel_hang_rejected_statically(self):
        source = make_source("hang.top") + "\nwhile True:\n    pass\n"
        self.make_plugin("hangtop", source=source)
        engine = discovery.Discovery(self.root, self.db)
        summary = await engine.refresh()
        self.assertEqual(self.code_for("hangtop"), ["E_CONTRACT_STATIC"])
        self.assertEqual(summary["valid"], 0)
        engine.close()

    async def test_metadata_hang_times_out_and_scan_continues(self):
        self.make_plugin("good", skill_id="good.skill")
        hang = make_source("hang.meta") + "\nimport time\n"
        hang = hang.replace(
            "def GetContextRequirements():\n    return {\"max_input_tokens\": 65536, \"max_output_tokens\": 4096, \"max_chat_messages\": 0, \"max_tool_calls\": 0}",
            "def GetContextRequirements():\n    time.sleep(60)\n    return {}",
        )
        self.make_plugin("hangmeta", source=hang)
        engine = discovery.Discovery(self.root, self.db, metadata_timeout=1.0)
        summary = await engine.refresh()
        self.assertEqual(self.code_for("hangmeta"), ["E_TIMEOUT"])
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["skill_ids"], ["good.skill"])
        engine.close()

    async def test_invalid_metadata_rejected(self):
        source = make_source("Bad.ID")
        self.make_plugin("badid", source=source)
        source2 = make_source("bad.name").replace('"name": "Skill"', '"name": 123')
        self.make_plugin("badname", source=source2)
        source3 = make_source("bad.schema").replace('"input_schema": {"type": "object", "additionalProperties": True}', '"input_schema": "nope"')
        self.make_plugin("badschema", source=source3)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("badid"), ["E_METADATA_INVALID"])
        self.assertEqual(self.code_for("badname"), ["E_METADATA_INVALID"])
        self.assertEqual(self.code_for("badschema"), ["E_METADATA_INVALID"])
        engine.close()

    async def test_manifest_invalid_and_missing(self):
        self.make_plugin("badjson", manifest=None, source=make_source("m.one"))
        (self.root / "badjson" / "manifest.json").write_text("{not json")
        self.make_plugin("wrongkey", manifest={"sha256": "0" * 64})
        self.make_plugin("extrakey", manifest={"module_sha256": "0" * 64, "extra": 1})
        (self.root / "nomanifest").mkdir()
        (self.root / "nomanifest" / "skill.py").write_text(make_source("m.two"))
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("badjson"), ["E_MANIFEST_INVALID"])
        self.assertEqual(self.code_for("wrongkey"), ["E_MANIFEST_INVALID"])
        self.assertEqual(self.code_for("extrakey"), ["E_MANIFEST_INVALID"])
        self.assertEqual(self.code_for("nomanifest"), ["E_MISSING_MANIFEST"])
        engine.close()

    async def test_missing_skill_py(self):
        (self.root / "noskill").mkdir()
        (self.root / "noskill" / "manifest.json").write_text(json.dumps({"module_sha256": "0" * 64}))
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("noskill"), ["E_MISSING_SKILL_PY"])
        engine.close()

    async def test_digest_mismatch(self):
        self.make_plugin("tampered", sha="0" * 64)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        rows = [row for row in self.rows() if row[1] == "tampered"]
        self.assertEqual(rows[0][5], "E_DIGEST_MISMATCH")
        self.assertEqual(rows[0][4], 0)
        self.assertNotEqual(rows[0][3], "0" * 64)
        self.assertEqual(engine.descriptors(), [])
        engine.close()

    async def test_duplicate_ids_reject_all(self):
        self.make_plugin("dup_a", skill_id="dup.skill")
        self.make_plugin("dup_b", skill_id="dup.skill")
        self.make_plugin("unique", skill_id="unique.skill")
        engine = discovery.Discovery(self.root, self.db)
        summary = await engine.refresh()
        self.assertEqual(self.code_for("dup_a"), ["E_DUPLICATE_ID"])
        self.assertEqual(self.code_for("dup_b"), ["E_DUPLICATE_ID"])
        self.assertEqual(summary["skill_ids"], ["unique.skill"])
        self.assertEqual(summary["error_codes"].get("E_DUPLICATE_ID"), 2)
        engine.close()

    async def test_reserved_ids_fail_closed(self):
        self.make_plugin("core", skill_id="harmonika.core")
        engine = discovery.Discovery(self.root, self.db, reserved_ids=("harmonika.core",))
        summary = await engine.refresh()
        self.assertEqual(self.code_for("core"), ["E_RESERVED_ID"])
        self.assertEqual(summary["valid"], 0)
        with self.assertRaises(discovery.SanitizedError) as caught:
            await engine.execute("harmonika.core", {})
        self.assertEqual(caught.exception.code, "E_NOT_REGISTERED")
        engine.close()

    async def test_symlink_and_hardlink_rejected(self):
        real = self._tmp.name + "/real_skill.py"
        with open(real, "w") as handle:
            handle.write(make_source("link.skill"))
        directory = self.make_plugin("linked", source=make_source("link.skill"))
        (directory / "skill.py").unlink()
        os.symlink(real, directory / "skill.py")
        hard = self.make_plugin("hardlinked", skill_id="hard.skill")
        os.link(hard / "skill.py", self._tmp.name + "/extra_link.py")
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(self.code_for("linked"), ["E_SYMLINK"])
        self.assertEqual(self.code_for("hardlinked"), ["E_SYMLINK"])
        engine.close()

    async def test_removal_stops_new_invokes(self):
        self.make_plugin("temp", skill_id="temp.skill")
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(len(engine.descriptors()), 1)
        shutil.rmtree(self.root / "temp")
        summary = await engine.refresh()
        self.assertEqual(summary["valid"], 0)
        self.assertEqual(engine.descriptors(), [])
        with self.assertRaises(discovery.SanitizedError) as caught:
            await engine.execute("temp.skill", {})
        self.assertEqual(caught.exception.code, "E_NOT_REGISTERED")
        self.assertEqual([row for row in self.rows() if row[1] == "temp"], [])
        engine.close()

    async def test_immutable_captured_bytes_and_toctou(self):
        self.make_plugin("frozen", skill_id="frozen.skill", marker="one")
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        self.assertEqual(await engine.execute("frozen.skill", {}), {"marker": "one"})
        directory = self.root / "frozen"
        (directory / "skill.py").write_text(make_source("frozen.skill", marker="two"))
        self.assertEqual(await engine.execute("frozen.skill", {}), {"marker": "one"})
        await engine.refresh()
        self.assertEqual(engine.descriptors(), [])
        self.assertEqual(self.code_for("frozen"), ["E_DIGEST_MISMATCH"])
        with self.assertRaises(discovery.SanitizedError):
            await engine.execute("frozen.skill", {})
        raw = (make_source("frozen.skill", marker="two")).encode("utf-8")
        (directory / "manifest.json").write_text(json.dumps({"module_sha256": hashlib.sha256(raw).hexdigest()}))
        summary = await engine.refresh()
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(await engine.execute("frozen.skill", {}), {"marker": "two"})
        engine.close()

    async def test_bounded_execute_runtime(self):
        source = make_source("slow.skill", marker="slow").replace(
            'return {"marker": "slow"}',
            "while True:\n        await asyncio.sleep(0.05)",
        )
        self.make_plugin("spinner", source=source)
        engine = discovery.Discovery(self.root, self.db, execute_timeout=1.5)
        await engine.refresh()
        with self.assertRaises(discovery.SanitizedError) as caught:
            await engine.execute("slow.skill", {})
        self.assertEqual(caught.exception.code, "E_TIMEOUT")
        engine.close()

    async def test_execute_error_sanitized(self):
        source = make_source("boom.skill", marker="boom").replace(
            'return {"marker": "boom"}', 'raise RuntimeError("X_EXC_SENTINEL")'
        )
        self.make_plugin("boomer", source=source)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        with self.assertRaises(discovery.SanitizedError) as caught:
            await engine.execute("boom.skill", {})
        self.assertEqual(caught.exception.code, "E_PLUGIN")
        self.assertEqual(str(caught.exception), "E_PLUGIN")
        with self.assertRaises(discovery.SanitizedError) as payload_error:
            await engine.execute("boom.skill", {"bad": object()})
        self.assertEqual(payload_error.exception.code, "E_PAYLOAD")
        with self.assertRaises(discovery.SanitizedError) as unknown:
            await engine.execute("missing.skill", {})
        self.assertEqual(unknown.exception.code, "E_NOT_REGISTERED")
        engine.close()

    async def test_closed_engine_rejects_use(self):
        self.make_plugin("closer", skill_id="close.skill")
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        engine.close()
        with self.assertRaises(discovery.SanitizedError) as caught:
            await engine.execute("close.skill", {})
        self.assertEqual(caught.exception.code, "E_CLOSED")
        with self.assertRaises(discovery.SanitizedError) as refresh_error:
            await engine.refresh()
        self.assertEqual(refresh_error.exception.code, "E_CLOSED")

    async def test_cold_start_persistence(self):
        self.make_plugin("persist", skill_id="persist.skill")
        engine = discovery.Discovery(self.root, self.db)
        first = await engine.refresh()
        engine.close()
        self.assertEqual(self.db.exists(), True)
        revived = discovery.Discovery(self.root, self.db)
        summary = revived.summary()
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary['registered'], 0)
        self.assertEqual(summary["finished_at"], first["finished_at"])
        self.assertTrue(summary.get("persisted"))
        self.assertEqual(revived.descriptors(), [])
        second = await revived.refresh()
        self.assertEqual(second["valid"], 1)
        self.assertEqual(await revived.execute("persist.skill", {}), {"marker": "ok"})
        revived.close()

    async def test_concurrent_scan_returns_busy(self):
        slow_source = make_source("slow.meta").replace(
            "def GetContextRequirements():",
            "def GetContextRequirements():\n    import time\n    time.sleep(1.5)",
        )
        self.make_plugin("slowmeta", source=slow_source)
        engine = discovery.Discovery(self.root, self.db, metadata_timeout=8.0)
        first = asyncio.ensure_future(engine.refresh())
        await asyncio.sleep(0.35)
        busy = await engine.refresh()
        self.assertEqual(busy["result"], "E_SCAN_BUSY")
        self.assertTrue(busy["scanning"])
        result = await first
        self.assertEqual(result["result"], "OK")
        self.assertEqual(result["valid"], 1)
        self.assertEqual(engine.summary()["result"], "OK")
        engine.close()

    async def test_scan_bounds_plugin_count(self):
        self.make_plugin("a", skill_id="a.skill")
        self.make_plugin("b", skill_id="b.skill")
        engine = discovery.Discovery(self.root, self.db, max_plugins=1)
        summary = await engine.refresh()
        self.assertEqual(summary["valid"], 0)
        self.assertTrue(summary["truncated"])
        engine.close()

    async def test_database_contains_no_raw_code_or_exceptions(self):
        self.make_plugin("secret", skill_id="secret.skill", marker="X_MARKER_SENTINEL")
        source = make_source("leak.skill") + "\nX_SRC_SENTINEL = 1\n"
        source = source.replace('return {"marker": "leak.skill"}', 'raise RuntimeError("X_EXC_SENTINEL")')
        self.make_plugin("leaker", source=source)
        engine = discovery.Discovery(self.root, self.db)
        await engine.refresh()
        try:
            await engine.execute("leak.skill", {"secret": "X_PAYLOAD_SENTINEL"})
        except discovery.SanitizedError:
            pass
        try:
            await engine.execute("secret.skill", {"secret": "X_PAYLOAD_SENTINEL"})
        except discovery.SanitizedError:
            pass
        text = self.dump_db_text()
        for sentinel in ("X_SRC_SENTINEL", "X_EXC_SENTINEL", "X_PAYLOAD_SENTINEL", "X_MARKER_SENTINEL", "Traceback", "while True"):
            self.assertNotIn(sentinel, text)

    async def test_existing_databases_preserved(self):
        session = sqlite3.connect(self.session_db)
        session.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, note TEXT)")
        session.execute("INSERT INTO sessions (note) VALUES ('keep-me')")
        session.commit()
        session.close()
        status = sqlite3.connect(self.db)
        status.execute("CREATE TABLE preexisting (value TEXT)")
        status.execute("INSERT INTO preexisting VALUES ('old-state')")
        status.commit()
        status.close()
        self.make_plugin("keep", skill_id="keep.skill")
        before=self.db.read_bytes();session_before=self.session_db.read_bytes()
        with self.assertRaises(discovery.SanitizedError):discovery.Discovery(self.root,self.db)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(self.session_db.read_bytes(),session_before)
        status=sqlite3.connect(self.db)
        self.assertEqual(status.execute('SELECT value FROM preexisting').fetchall(),[('old-state',)])
        self.assertEqual(status.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('preexisting',)])
        status.close()


if __name__ == "__main__":
    unittest.main()
