"""Scaling, lazy-source and fail-closed tests for the chunked scanner.

Builds 2600+ literal modules derived from the text-metrics fixture and wires
``scanning.scan`` into ``Discovery._scan_sync`` the same way the root
integration will (``types.MethodType``). Plugin execution always goes through
the isolated child; the parent never imports or evaluates plugin code.
"""

import asyncio
import hashlib
import json
import os
import random
import re
import resource
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.engine_registry import discovery, scanning
from backend.engine_registry.discovery import Discovery, SanitizedError
from backend.engine_registry.scanning import CapturedSource, scan

TEMPLATE = r'''"""Pure text counts; operator installed literal skill."""
import re

def GetSkillIdentity():
 return {'id':'IDVAL','name':'NAMEVAL','core_capabilities':['text_metrics'],
         'requirements':[],
         'input_schema':{'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':12000}},'required':['text'],'additionalProperties':False}}

def GetContextRequirements():
 return {'max_input_tokens':65536,'max_output_tokens':4096,'max_chat_messages':0,'max_tool_calls':0}

async def Execute(payload):
 text=payload['text']
 return {'characters':len(text),'words':len(re.findall(r'\S+',text))}
'''

DYNAMIC_METADATA = r'''"""Metadata built at runtime; must use the isolated child audit."""
import re

_META_ID = 'dynamic.skill'

def GetSkillIdentity():
 return {'id':_META_ID,'name':'Dynamic','core_capabilities':['text_metrics'],
         'requirements':[],
         'input_schema':{'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':12000}},'required':['text'],'additionalProperties':False}}

def GetContextRequirements():
 return {'max_input_tokens':65536,'max_output_tokens':4096,'max_chat_messages':0,'max_tool_calls':0}

async def Execute(payload):
 text=payload['text']
 return {'characters':len(text),'words':len(re.findall(r'\S+',text))}
'''

SCALE_COUNT = 2600


def make_source(skill_id, name="Skill"):
    return TEMPLATE.replace("IDVAL", skill_id).replace("NAMEVAL", name)


def build_modules(root, start, count, prefix="scale"):
    for index in range(start, start + count):
        folder = Path(root) / ("m%05d" % index)
        folder.mkdir(parents=True, exist_ok=True)
        raw = make_source("%s.s%05d" % (prefix, index), "Skill %05d" % index).encode("utf-8")
        (folder / "skill.py").write_bytes(raw)
        (folder / "manifest.json").write_text(json.dumps({"module_sha256": hashlib.sha256(raw).hexdigest()}))


def peak_rss_bytes():
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage if sys.platform == "darwin" else usage * 1024


class ScannerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "skills"
        self.root.mkdir()
        self.db = self.base / "status.sqlite3"

    def make_engine(self, root=None, db=None, **kwargs):
        engine = Discovery(root or self.root, db or self.db, **kwargs)
        engine._scan_sync = types.MethodType(scan, engine)
        return engine

    def plugin(self, dirname, source, root=None):
        folder = (root or self.root) / dirname
        folder.mkdir(parents=True, exist_ok=True)
        raw = source.encode("utf-8") if isinstance(source, str) else source
        (folder / "skill.py").write_bytes(raw)
        (folder / "manifest.json").write_text(json.dumps({"module_sha256": hashlib.sha256(raw).hexdigest()}))
        return folder

    def spool_dir(self, db=None):
        return Path(db or self.db).parent / "discovery-source"

    def rows(self, db=None):
        connection = sqlite3.connect(db or self.db)
        try:
            return connection.execute(
                "SELECT root, directory, skill_id, sha256, hash_verified, error_code, audit_latency_ms, updated_at"
                " FROM engine_discovery_status"
            ).fetchall()
        finally:
            connection.close()

    def code_for(self, dirname, db=None):
        return sorted(row[5] for row in self.rows(db) if row[1] == dirname)

    async def lazy_execute(self, engine, skill_id, payload):
        entry = engine._snapshot[skill_id]
        blob = entry.source.load_checked()
        verdict = await engine._run_child_async("execute", blob, payload)
        return verdict["result"]


class ContractPreservation(ScannerCase):
    async def test_toplevel_reassignment_rejected_by_child_audit(self):
        variants = [
            "\nExecute=3\n",
            "\nasync def alternate(first,second):\n return {}\nExecute=alternate\n",
            "\nGetSkillIdentity=3\n",
        ]
        for index, tail in enumerate(variants):
            self.plugin("v%03d" % index, make_source("contract.s%03d" % index) + tail)
        engine = self.make_engine()
        summary = await engine.refresh()
        self.assertEqual(summary["registered"], 0)
        self.assertEqual(summary["error_codes"], {"E_PLUGIN": 3})
        self.assertEqual(summary["metadata_subprocess"], 3)
        self.assertEqual(engine.descriptors(), [])
        engine.close()

    async def test_dynamic_metadata_falls_back_and_registers(self):
        self.plugin("dynamic", DYNAMIC_METADATA)
        self.plugin("osimport", make_source("osimport.skill") + "\nimport os\n")
        engine = self.make_engine()
        summary = await engine.refresh()
        self.assertEqual(summary["registered"], 2)
        self.assertEqual(summary["metadata_static"], 0)
        self.assertEqual(summary["metadata_subprocess"], 2)
        descriptor = {row["id"]: row for row in engine.descriptors()}
        self.assertEqual(descriptor["dynamic.skill"]["name"], "Dynamic")
        self.assertEqual(descriptor["dynamic.skill"]["sha256"], hashlib.sha256(DYNAMIC_METADATA.encode()).hexdigest())
        self.assertEqual(descriptor["osimport.skill"]["id"], "osimport.skill")
        engine.close()

    async def test_scanner_never_evals_or_execs_in_parent(self):
        text = Path(scanning.__file__).read_text()
        self.assertIsNone(re.search(r"(?<![\w.])eval\(", text))
        self.assertIsNone(re.search(r"(?<![\w.])exec\(", text))
        self.assertIsNone(re.search(r"(?<![\w.])compile\(", text))


class DuplicateAndBounds(ScannerCase):
    async def test_duplicate_ids_across_far_chunks(self):
        self.plugin("a0000", make_source("dup.far", "Dup"))
        for index in range(1, 131):
            self.plugin("f%04d" % index, make_source("fill.s%04d" % index))
        self.plugin("z9999", make_source("dup.far", "Dup"))
        engine = self.make_engine()
        summary = await engine.refresh()
        self.assertEqual(summary["chunk_size"], 64)
        self.assertEqual(summary["registered"], 130)
        self.assertEqual(summary["error_codes"].get("E_DUPLICATE_ID"), 2)
        self.assertEqual(self.code_for("a0000"), ["E_DUPLICATE_ID"])
        self.assertEqual(self.code_for("z9999"), ["E_DUPLICATE_ID"])
        self.assertNotIn("dup.far", engine._snapshot)
        engine.close()

    async def test_max_plugins_bound_fails_closed(self):
        for index in range(5):
            self.plugin("mod%d" % index, make_source("bound.s%d" % index))
        engine = self.make_engine(max_plugins=2)
        summary = await engine.refresh()
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["registered"], 0)
        self.assertEqual(summary["result"], "E_BOUNDS")
        self.assertEqual(engine.descriptors(), [])
        engine.close()

    async def test_total_bytes_bound_fails_closed(self):
        self.plugin("big", make_source("bound.big"))
        engine = self.make_engine(max_total_bytes=10)
        summary = await engine.refresh()
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["registered"], 0)
        self.assertEqual(engine._snapshot, {})
        engine.close()

    async def test_scan_wallclock_timeout_fails_closed(self):
        self.plugin("solo", make_source("timeout.solo"))
        engine = self.make_engine(scan_timeout=-1)
        summary = await engine.refresh()
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["result"], "E_SCAN_TIMEOUT")
        self.assertEqual(summary["registered"], 0)
        self.assertEqual(engine.descriptors(), [])
        engine.close()


class TamperAndSources(ScannerCase):
    async def test_hash_tamper_withdraws_entry_and_prunes_spool(self):
        build_modules(self.root, 0, 70)
        engine = self.make_engine()
        first = await engine.refresh()
        self.assertEqual(first["registered"], 70)
        victim = self.root / "m00003"
        original = (victim / "skill.py").read_bytes()
        victim_digest = hashlib.sha256(original).hexdigest()
        (victim / "skill.py").write_bytes(make_source("scale.s00003", "Tampered").encode("utf-8"))
        second = await engine.refresh()
        self.assertEqual(second["registered"], 69)
        self.assertEqual(second["error_codes"].get("E_DIGEST_MISMATCH"), 1)
        self.assertEqual(self.code_for("m00003"), ["E_DIGEST_MISMATCH"])
        self.assertNotIn("scale.s00003", engine._snapshot)
        with self.assertRaises(SanitizedError) as caught:
            await engine.execute("scale.s00003", {})
        self.assertEqual(caught.exception.code, "E_NOT_REGISTERED")
        self.assertTrue((self.spool_dir() / victim_digest).exists())
        third = await engine.refresh()
        self.assertEqual(third["registered"], 69)
        self.assertFalse((self.spool_dir() / victim_digest).exists())
        result = await self.lazy_execute(engine, "scale.s00042", {"text": "Halo Kak!"})
        self.assertEqual(result["words"], 2)
        engine.close()

    async def test_captured_source_tamper_fail_closed(self):
        self.plugin("frozen", make_source("frozen.skill"))
        engine = self.make_engine()
        await engine.refresh()
        entry = engine._snapshot["frozen.skill"]
        self.assertIsInstance(entry.source, CapturedSource)
        self.assertEqual(Path(entry.source.path).parent, self.spool_dir())
        original = entry.source.load_checked()

        os.chmod(entry.source.path, 0o644)
        with self.assertRaises(SanitizedError) as caught:
            entry.source.load_checked()
        self.assertEqual(caught.exception.code, "E_UNTRUSTED_OWNER")
        os.chmod(entry.source.path, 0o600)

        same_length = original.replace(b"Pure text counts", b"pure text counts")
        self.assertEqual(len(same_length), len(original))
        Path(entry.source.path).write_bytes(same_length)
        with self.assertRaises(SanitizedError) as caught:
            entry.source.load_checked()
        self.assertEqual(caught.exception.code, "E_DIGEST_MISMATCH")
        Path(entry.source.path).write_bytes(original)

        with self.assertRaises(SanitizedError) as caught:
            entry.source.load_checked(max_bytes=len(original) - 1)
        self.assertIn(caught.exception.code, ("E_BOUNDS", "E_DIGEST_MISMATCH"))

        linked = self.base / "linked-target"
        linked.write_bytes(original)
        os.unlink(entry.source.path)
        os.symlink(linked, entry.source.path)
        with self.assertRaises(SanitizedError) as caught:
            entry.source.load_checked()
        self.assertEqual(caught.exception.code, "E_SYMLINK")
        os.unlink(entry.source.path)

        Path(entry.source.path).write_bytes(original)
        os.chmod(entry.source.path, 0o600)
        self.assertEqual(entry.source.load_checked(), original)
        engine.close()

    async def test_spool_permissions_are_private(self):
        self.plugin("locked", make_source("locked.skill"))
        engine = self.make_engine()
        await engine.refresh()
        spool = self.spool_dir()
        self.assertTrue(spool.is_dir())
        self.assertEqual(stat.S_IMODE(os.stat(spool).st_mode) & 0o777, 0o700)
        entry = engine._snapshot["locked.skill"]
        self.assertEqual(stat.S_IMODE(os.stat(entry.source.path).st_mode) & 0o777, 0o600)
        engine.close()


class PruneAndLeases(ScannerCase):
    async def test_prune_retains_leases_and_old_snapshot(self):
        self.plugin("a_dir", make_source("keep.a"))
        self.plugin("b_dir", make_source("keep.b"))
        engine = self.make_engine()
        engine._source_leases = {}
        engine._source_lease_lock = threading.Lock()
        await engine.refresh()
        digest_a = engine._snapshot["keep.a"].descriptor["sha256"]
        digest_b = engine._snapshot["keep.b"].descriptor["sha256"]
        spool = self.spool_dir()
        self.assertTrue((spool / digest_a).exists())
        self.assertTrue((spool / digest_b).exists())

        engine._source_leases[digest_a] = 1
        shutil.rmtree(self.root / "b_dir")
        self.plugin("c_dir", make_source("keep.c"))
        await engine.refresh()
        digest_c = engine._snapshot["keep.c"].descriptor["sha256"]
        self.assertNotIn("keep.b", engine._snapshot)
        self.assertTrue((spool / digest_b).exists())

        await engine.refresh()
        self.assertFalse((spool / digest_b).exists())
        self.assertTrue((spool / digest_a).exists())
        self.assertTrue((spool / digest_c).exists())

        shutil.rmtree(self.root / "a_dir")
        await engine.refresh()
        self.assertNotIn("keep.a", engine._snapshot)
        self.assertTrue((spool / digest_a).exists())
        self.assertTrue((spool / digest_c).exists())
        engine._source_leases.clear()
        await engine.refresh()
        self.assertFalse((spool / digest_a).exists())
        self.assertTrue((spool / digest_c).exists())
        engine.close()

    async def test_prune_never_touches_foreign_files(self):
        self.plugin("solo", make_source("prune.solo"))
        engine = self.make_engine()
        await engine.refresh()
        spool = self.spool_dir()
        notes = spool / "operator-notes.txt"
        notes.write_text("keep me")
        foreign_hex_dir = spool / ("d" * 64)
        foreign_hex_dir.mkdir()
        await engine.refresh()
        self.assertTrue(notes.exists())
        self.assertTrue(foreign_hex_dir.is_dir())
        engine.close()


class SummaryContract(ScannerCase):
    async def test_summary_shape_and_sanitized_output(self):
        self.plugin("solo", make_source("summary.solo"))
        engine = self.make_engine()
        summary = await engine.refresh()
        self.assertTrue(set(engine._default_summary()) <= set(summary))
        for key in (
            "chunk_size",
            "lazy_source_storage",
            "source_bytes",
            "metadata_bytes",
            "metadata_static",
            "metadata_cached",
            "metadata_subprocess",
        ):
            self.assertIn(key, summary)
        self.assertEqual(summary["chunk_size"], 64)
        self.assertTrue(summary["lazy_source_storage"])
        self.assertGreater(summary["source_bytes"], 0)
        self.assertGreater(summary["metadata_bytes"], 0)
        self.assertLessEqual(summary["metadata_bytes"], 8 * 1024 * 1024)
        payload = json.dumps(summary)
        self.assertNotIn(str(self.base), payload)
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("import re", payload)
        engine.close()

    async def test_chunk_size_is_configurable(self):
        build_modules(self.root, 0, 40, prefix="chunked")
        engine = Discovery(self.root, self.db, max_plugins=64)
        engine._scan_sync = types.MethodType(lambda self: scan(self, chunk_size=16), engine)
        summary = await engine.refresh()
        self.assertEqual(summary["chunk_size"], 16)
        self.assertEqual(summary["registered"], 40)
        engine.close()

    async def test_metadata_cache_budget_bounded(self):
        build_modules(self.root, 0, 120, prefix="budget")
        engine = self.make_engine()
        with patch.object(Discovery, "_run_child_sync", new=MagicMock()):
            summary = await engine.refresh()
        self.assertEqual(summary["registered"], 120)
        self.assertLessEqual(summary["metadata_bytes"], 8 * 1024 * 1024)
        serialized = sum(
            len(json.dumps(blob, ensure_ascii=True)) for blob in engine._metadata_cache.values()
        )
        self.assertLessEqual(serialized, 8 * 1024 * 1024)
        engine.close()


class ScaleSmoke(ScannerCase):
    @classmethod
    def setUpClass(cls):
        cls._class_tmp = tempfile.TemporaryDirectory()
        base = Path(cls._class_tmp.name).resolve()
        cls.root = base / "skills"
        cls.root.mkdir()
        build_modules(cls.root, 0, SCALE_COUNT)

    @classmethod
    def tearDownClass(cls):
        cls._class_tmp.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.db = self.base / "status.sqlite3"

    async def test_first_scan_registers_all_2600_within_bound(self):
        engine = self.make_engine(max_plugins=4096)
        before = peak_rss_bytes()
        with patch.object(Discovery, "_run_child_sync", new=MagicMock()) as child:
            wall = time.monotonic()
            summary = await engine.refresh()
            wall = time.monotonic() - wall
        self.assertEqual(child.call_count, 0)
        self.assertEqual(summary["result"], "OK")
        self.assertEqual(summary["registered"], SCALE_COUNT)
        self.assertEqual(summary["valid"], SCALE_COUNT)
        self.assertFalse(summary["truncated"])
        self.assertEqual(summary["scanned_dirs"], SCALE_COUNT)
        self.assertEqual(summary["metadata_static"], SCALE_COUNT)
        self.assertEqual(summary["metadata_subprocess"], 0)
        self.assertGreater(summary["source_bytes"], 0)
        self.assertLessEqual(summary["source_bytes"], 64 * 1024 * 1024)
        self.assertLessEqual(summary["metadata_bytes"], 8 * 1024 * 1024)
        self.assertLessEqual(summary["duration_ms"], 30000)
        self.assertLessEqual(wall, 30)
        growth = peak_rss_bytes() - before
        self.assertLessEqual(growth, 256 * 1024 * 1024)
        self.assertEqual(len(engine.descriptors()), SCALE_COUNT)
        self.assertEqual(len(engine._snapshot), SCALE_COUNT)
        engine.close()

    async def test_warm_rescan_uses_cache_without_subprocess(self):
        engine = self.make_engine(max_plugins=4096)
        first = await engine.refresh()
        self.assertEqual(first["metadata_static"], SCALE_COUNT)
        with patch.object(Discovery, "_run_child_sync", new=MagicMock()) as child:
            second = await engine.refresh()
        self.assertEqual(child.call_count, 0)
        self.assertEqual(second["result"], "OK")
        self.assertEqual(second["registered"], SCALE_COUNT)
        self.assertEqual(second["metadata_cached"], SCALE_COUNT)
        self.assertEqual(second["metadata_subprocess"], 0)
        self.assertLess(second["duration_ms"], 30000)
        engine.close()

    async def test_lazy_source_load_executes_random_module(self):
        engine = self.make_engine(max_plugins=4096)
        summary = await engine.refresh()
        self.assertEqual(summary["registered"], SCALE_COUNT)
        pick = random.Random(20261025).randrange(SCALE_COUNT)
        skill_id = "scale.s%05d" % pick
        entry = engine._snapshot[skill_id]
        self.assertIsInstance(entry.source, CapturedSource)
        self.assertEqual(Path(entry.source.path).parent, self.spool_dir())
        blob = entry.source.load_checked()
        self.assertEqual(hashlib.sha256(blob).hexdigest(), entry.descriptor["sha256"])
        self.assertEqual(blob, (self.root / ("m%05d" % pick) / "skill.py").read_bytes())
        result = await self.lazy_execute(engine, skill_id, {"text": "Halo Kak!"})
        self.assertEqual(result["characters"], 9)
        self.assertEqual(result["words"], 2)
        engine.close()


if __name__ == "__main__":
    unittest.main()
