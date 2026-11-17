"""Tenant boundary tests using fictional notes, temp SQLite, and stub embeddings."""
import importlib.util
import json
from pathlib import Path
import os
import sqlite3
import struct
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "services/knowledge.py"
SPEC = importlib.util.spec_from_file_location("harmonika_knowledge_test", SOURCE)
KB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KB)


def embedding(_text):
    return [1.0] + [0.0] * 383


class KnowledgeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "knowledge.sqlite")
        self.store = KB.KnowledgeBase(self.path, embedder=embedding)
        self.addCleanup(self.store.close)

    def test_writer_and_other_token_cannot_cross_read_or_delete(self):
        first = self.store.store("token:1", "SYNTHETIC_FIRST_NOTE", tags=["a"])
        second = self.store.store("token:2", "SYNTHETIC_SECOND_NOTE", tags=["b"])
        self.assertEqual([row["id"] for row in self.store.search("token:1", "query")], [first])
        self.assertEqual([row["id"] for row in self.store.search("token:2", "query")], [second])
        self.assertEqual([row["id"] for row in self.store.list("token:1")], [first])
        self.assertFalse(self.store.delete("token:2", first))
        self.assertEqual(self.store.count("token:1"), 1)
        self.assertTrue(self.store.delete("token:1", first))
        self.assertEqual(self.store.list("token:1"), [])
        self.assertEqual(self.store.count("token:2"), 1)

    def test_invalid_owner_rejected_before_embedding_or_db_effects(self):
        with patch.object(self.store, "embed", side_effect=AssertionError("embedding must not execute")):
            for owner in [None, "", "token:0", "token:-1", "token:1 OR 1=1", "token:1\n", "customer:1"]:
                for call in [lambda: self.store.store(owner, "synthetic"),
                             lambda: self.store.search(owner, "synthetic"),
                             lambda: self.store.list(owner),
                             lambda: self.store.delete(owner, 1)]:
                    with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, "owner_required"):
                        call()
        self.assertEqual(self.store.count(), 0)

    def test_search_is_bounded_within_owner_and_does_not_silently_drop_results(self):
        self.store.max_search_rows = 2
        for index in range(3):
            self.store.store("token:1", "synthetic note " + str(index))
        own = self.store.store("token:2", "synthetic isolated note")
        with self.assertRaisesRegex(ValueError, "search_scope_limit"):
            self.store.search("token:1", "query")
        self.assertEqual([row["id"] for row in self.store.search("token:2", "query")], [own])
        for value in [0, -1, True, 1.5, 101, "-1", "1;SELECT"]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_limit"):
                self.store.list("token:1", value)
        with self.assertRaisesRegex(ValueError, "invalid_limit"):
            self.store.search("token:1", "query", 21)

    def test_search_filters_are_bound_values_not_sql(self):
        self.store.store("token:1", "synthetic note", kind="normal", tags=["a"])
        self.assertEqual(self.store.search("token:1", "query", kind="x' OR 1=1 --"), [])
        self.assertEqual(len(self.store.search("token:1", "query", kind="normal", tag="a")), 1)
        self.assertEqual(self.store.search("token:1", "query", tag="b"), [])

    def test_note_and_embedding_validation_is_explicit(self):
        for kwargs in [{"text": ""}, {"text": "x" * 60001}, {"text": "x", "tags": "wrong"},
                       {"text": "x", "tags": [1]}, {"text": "x", "kind": ""}]:
            with self.subTest(kwargs={k: type(v).__name__ for k, v in kwargs.items()}), self.assertRaises(ValueError):
                self.store.store("token:1", **kwargs)
        with patch.object(self.store, "embed", return_value=[float("nan")] * 384):
            with self.assertRaisesRegex(ValueError, "embedding_invalid"):
                self.store.store("token:1", "synthetic")
        self.assertEqual(self.store.count(), 0)

    def test_rerank_pool_contains_only_owner_filtered_candidates_and_reorders_valid_indices(self):
        own = [self.store.store("token:1", "SYNTHETIC_OWNER_A_" + str(i), kind="selected", tags=["chosen"])
               for i in range(25)]
        self.store.store("token:1", "SYNTHETIC_OTHER_KIND", kind="other", tags=["chosen"])
        self.store.store("token:1", "SYNTHETIC_OTHER_TAG", kind="selected", tags=["other"])
        self.store.store("token:2", "SYNTHETIC_OWNER_B", kind="selected", tags=["chosen"])
        metadata = {}
        seen = []
        def rank(query, texts, top_k):
            seen.extend(texts)
            self.assertEqual(query, "query")
            self.assertEqual(top_k, 20)
            return [{"index": i, "score": float(i)} for i in reversed(range(top_k))]
        with patch.object(KB, "rerank_via_service", side_effect=rank):
            result = self.store.search("token:1", "query", limit=2, kind="selected", tag="chosen",
                                       rerank=True, metadata=metadata)
        self.assertEqual(len(seen), 20)
        self.assertTrue(all(text.startswith("SYNTHETIC_OWNER_A_") for text in seen))
        self.assertEqual([row["id"] for row in result], own[5:7])
        self.assertEqual([row["rerank_score"] for row in result], [19.0, 18.0])
        self.assertEqual(metadata, {"rerank_requested": True, "rerank_used": True})

    def test_invalid_or_unavailable_rerank_falls_back_without_other_owner_notes(self):
        self.store.store("token:1", "SYNTHETIC_A_ONE")
        self.store.store("token:1", "SYNTHETIC_A_TWO")
        self.store.store("token:2", "SYNTHETIC_B")
        expected = self.store.search("token:1", "query")
        cases = [None, [], [{"index": 0, "score": 1}],
                 [{"index": -1, "score": 2}, {"index": 0, "score": 1}],
                 [{"index": 0, "score": 2}, {"index": 0, "score": 1}],
                 [{"index": True, "score": 2}, {"index": 0, "score": 1}],
                 [{"index": 1, "score": float("nan")}, {"index": 0, "score": 1}],
                 [{"index": 1, "score": "2"}, {"index": 0, "score": 1}],
                 RuntimeError("SYNTHETIC_PRIVATE_RERANK_FAILURE")]
        for response in cases:
            metadata = {}
            config = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with self.subTest(case=type(response).__name__), patch.object(KB, "rerank_via_service", **config):
                result = self.store.search("token:1", "query", rerank=True, metadata=metadata)
                self.assertEqual(result, expected)
                self.assertEqual(metadata, {"rerank_requested": True, "rerank_used": False})
        with patch.object(KB, "rerank_via_service") as rank:
            self.assertEqual(self.store.search("token:1", "query", rerank=False), expected)
            rank.assert_not_called()


class LegacyMigrationTests(unittest.TestCase):
    def test_additive_migration_retains_legacy_row_and_hides_it_from_all_tenants(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.sqlite")
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT,kind TEXT,tags TEXT,text TEXT,vector BLOB,created_at INTEGER)")
            old.execute("INSERT INTO chunks(source,kind,tags,text,vector,created_at) VALUES (?,?,?,?,?,?)",
                        ("synthetic legacy", "note", "[]", "SYNTHETIC_UNOWNED_NOTE", struct.pack("384f", *embedding("")), 1))
            old.commit();old.close()
            os.chmod(path, 0o644)
            for _ in range(2):
                store = KB.KnowledgeBase(path, embedder=embedding)
                try:
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
                    self.assertEqual(store.count(), 1)
                    self.assertEqual(store.list("token:1"), [])
                    self.assertEqual(store.search("token:2", "query"), [])
                    self.assertFalse(store.delete("token:1", 1))
                    self.assertEqual(store.db.execute("SELECT owner,text FROM chunks WHERE id=1").fetchone(),
                                     ("", "SYNTHETIC_UNOWNED_NOTE"))
                finally:
                    store.close()

    def test_database_path_override_is_read_without_import_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "new.sqlite")
            with patch.dict("os.environ", {"HARMONIKA_KB_DB_PATH": path}):
                spec = importlib.util.spec_from_file_location("isolated_knowledge_import", SOURCE)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            self.assertEqual(module.DB_PATH, path)
            self.assertFalse(Path(path).exists())


class KnowledgeHTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = KB.make_server(str(Path(self.temp.name) / "http.sqlite"), port=0, embedder=embedding)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.server.knowledge.close()

    def call(self, route, owner=None, body=None):
        headers = {"Content-Type": "application/json"}
        if owner is not None:
            headers[KB.OWNER_HEADER] = owner
        data = None if body is None else json.dumps(body).encode()
        request = Request("http://127.0.0.1:" + str(self.server.server_address[1]) + route,
                          data=data, headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_http_calls_never_accept_body_or_query_owner(self):
        status, saved = self.call("/store", "token:1", {"text": "SYNTHETIC_OWNER_A"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("/list", "token:2"), (200, {"items": []}))
        self.assertEqual(self.call("/search", "token:2", {"query": "owner"}), (200, {"results": []}))
        self.assertEqual(self.call("/delete", "token:2", {"id": saved["id"]}), (200, {"deleted": False}))
        self.assertEqual(self.call("/store", "token:2", {"text": "x", "owner": "token:1"})[0], 400)
        self.assertEqual(self.call("/list?owner=token%3A1", "token:2")[0], 400)
        self.assertEqual(len(self.call("/list", "token:1")[1]["items"]), 1)

    def test_missing_owner_denied_and_errors_do_not_echo_backend_details(self):
        for route, body in [("/list", None), ("/store", {"text": "x"}), ("/search", {"query": "x"}), ("/delete", {"id": 1})]:
            self.assertEqual(self.call(route, body=body), (403, {"error": "owner_required"}))
        with patch.object(self.server.knowledge, "embed", side_effect=RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION")):
            self.assertEqual(self.call("/store", "token:1", {"text": "x"}), (503, {"error": "knowledge_unavailable"}))
        self.assertEqual(self.call("/store", "token:1", ["not an object"]), (400, {"error": "invalid_fields"}))
        status, health = self.call("/health")
        self.assertEqual(status, 200)
        self.assertEqual(set(health), {"ok", "service", "entries"})

    def test_http_rerank_option_is_preserved_and_reports_actual_fallback(self):
        for text in ["SYNTHETIC_A_ONE", "SYNTHETIC_A_TWO"]:
            self.call("/store", "token:1", {"text": text})
        self.call("/store", "token:2", {"text": "SYNTHETIC_B"})
        with patch.object(KB, "rerank_via_service", return_value=[{"index": 1, "score": 2.0}, {"index": 0, "score": 1.0}]) as rank:
            status, result = self.call("/search", "token:1", {"query": "query", "rerank": True})
            self.assertEqual(status, 200)
            self.assertTrue(result["rerank_requested"])
            self.assertTrue(result["rerank_used"])
            self.assertEqual(rank.call_args[0][1], ["SYNTHETIC_A_TWO", "SYNTHETIC_A_ONE"])
            self.assertTrue(all(row["text"].startswith("SYNTHETIC_A_") for row in result["results"]))
        with patch.object(KB, "rerank_via_service", side_effect=RuntimeError("SYNTHETIC_PRIVATE_FAILURE")):
            status, result = self.call("/search", "token:1", {"query": "query", "rerank": True})
            self.assertEqual(status, 200)
            self.assertTrue(result["rerank_requested"])
            self.assertFalse(result["rerank_used"])
            self.assertEqual(len(result["results"]), 2)
            self.assertNotIn("SYNTHETIC_PRIVATE_FAILURE", json.dumps(result))
        self.assertEqual(self.call("/search", "token:1", {"query": "query", "rerank": "yes"}),
                         (400, {"error": "invalid_fields"}))


if __name__ == "__main__":
    unittest.main()
