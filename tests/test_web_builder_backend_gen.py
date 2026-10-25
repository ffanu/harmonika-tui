"""Generator tests for the Harmonika web-builder backend/database skills (IDs 72-96).

Verifies real behavior, not presence:
- every generated Python file compiles; SQL artifacts execute on SQLite
- generated stdlib modules run for real: repository/service CRUD roundtrip,
  queue retry + dead-letter, cron due-scheduling, upload bounds, payment
  signature verification + trust policy, OAuth allowlist/PKCE/state,
  JWT lifecycle with a strict PyJWT-compatible shim
- migration/seed/backup runner roundtrips via subprocess on temp databases
- credentials fail closed, injection is neutralized, paths stay contained
- db.run.* skills return recipes with executed=False (root sandbox executes)

Runs on Python 3.9+ with the standard library only (the generated FastAPI
source is compile-checked; executing it needs the pinned root image).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "backend", "web_builder", "templates")


from backend.web_builder.templates import backend_api, database  # noqa: E402

assert backend_api.__file__.startswith(ROOT), backend_api.__file__
assert database.__file__.startswith(ROOT), database.__file__

EXPECTED_IDS = [
    ("be.gen.api.crud", 72), ("be.gen.api.custom", 73), ("be.gen.auth.jwt", 74),
    ("be.gen.auth.oauth", 75), ("be.gen.middleware", 76), ("be.gen.validation", 77),
    ("be.gen.orm.model", 78), ("be.gen.service", 79), ("be.gen.controller", 80),
    ("be.gen.dto", 81), ("be.gen.websocket", 82), ("be.gen.cron", 83),
    ("be.gen.queue", 84), ("be.gen.upload", 85), ("be.gen.payment", 86),
    ("db.gen.schema", 87), ("db.gen.migration", 88), ("db.gen.seed", 89),
    ("db.run.migration", 90), ("db.run.seed", 91), ("db.gen.indexes", 92),
    ("db.gen.relations", 93), ("db.gen.triggers", 94), ("db.gen.stored_proc", 95),
    ("db.backup.automate", 96),
]

EXPECTED_REQUIREMENTS = ("fastapi==0.162.0", "uvicorn==0.54.0",
                         "pydantic>=2,<3", "PyJWT==2.10.1")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class Env:
    """Scoped os.environ override with restore-on-exit."""

    def __init__(self, **values):
        self.values = values
        self.old = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.old[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def materialize(writes, root):
    root_real = os.path.realpath(root)
    for write in writes:
        target = os.path.realpath(os.path.join(root, write["path"]))
        if not target.startswith(root_real + os.sep):
            raise AssertionError("write escapes project root: %s" % write["path"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as handle:
            handle.write(write["content"])
    return root


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_py(script, *args):
    return subprocess.run([sys.executable, script] + list(args),
                          capture_output=True, text=True, timeout=90)


def all_writes(args=None):
    for skill_id, skill in sorted(backend_api.SKILLS.items(),
                                  key=lambda kv: kv[1]["number"]):
        yield skill_id, skill["handler"](dict(args or {}))
    for skill_id, skill in sorted(database.SKILLS.items(),
                                  key=lambda kv: kv[1]["number"]):
        yield skill_id, skill["handler"](dict(args or {}))


def make_jwt_shim():
    """Minimal PyJWT-compatible HS256 shim (encode/decode + error types)."""

    class InvalidTokenError(Exception):
        pass

    class ExpiredSignatureError(InvalidTokenError):
        pass

    def _b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    def _unb64(text):
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    def encode(payload, key, algorithm="HS256"):
        assert algorithm == "HS256"
        signing = (_b64(json.dumps({"alg": "HS256", "typ": "JWT"},
                                   separators=(",", ":")).encode())
                   + b"." + _b64(json.dumps(payload, separators=(",", ":")).encode()))
        signature = hmac.new(key.encode(), signing, hashlib.sha256).digest()
        return (signing + b"." + _b64(signature)).decode()

    def decode(token, key, algorithms=None, options=None, **kwargs):
        if algorithms != ["HS256"]:
            raise InvalidTokenError("algorithm_not_allowed")
        try:
            head, body, signature = token.split(".")
        except ValueError:
            raise InvalidTokenError("malformed")
        expected = hmac.new(key.encode(), (head + "." + body).encode(),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            raise InvalidTokenError("bad_signature")
        payload = json.loads(_unb64(body))
        for claim in (options or {}).get("require", []):
            if claim not in payload:
                raise InvalidTokenError("missing_claim:" + claim)
        if "exp" in payload and payload["exp"] <= time.time():
            raise ExpiredSignatureError("expired")
        return payload

    shim = types.ModuleType("jwt")
    shim.encode = encode
    shim.decode = decode
    shim.InvalidTokenError = InvalidTokenError
    shim.ExpiredSignatureError = ExpiredSignatureError
    return shim


def exec_auth_source(src, name, env):
    shim = make_jwt_shim()
    saved = sys.modules.get("jwt")
    sys.modules["jwt"] = shim
    try:
        with Env(**env):
            namespace = {"__name__": name}
            exec(compile(src, name, "exec"), namespace)
    finally:
        if saved is None:
            sys.modules.pop("jwt", None)
        else:
            sys.modules["jwt"] = saved
    return namespace


def purge_app_modules():
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# catalog contract
# ---------------------------------------------------------------------------

class TestCatalogContract(unittest.TestCase):
    def test_all_25_ids_with_exact_numbers(self):
        combined = {}
        combined.update({k: v["number"] for k, v in backend_api.SKILLS.items()})
        combined.update({k: v["number"] for k, v in database.SKILLS.items()})
        self.assertEqual(len(combined), 25)
        for skill_id, number in EXPECTED_IDS:
            self.assertEqual(combined.get(skill_id), number,
                             "wrong number for %s" % skill_id)
        self.assertEqual(sorted(combined.values()), list(range(72, 97)))
        self.assertEqual(len(backend_api.SKILLS), 15)
        self.assertEqual(len(database.SKILLS), 10)

    def test_entry_shape_and_strict_schemas(self):
        for module in (backend_api, database):
            for skill_id, entry in module.SKILLS.items():
                self.assertIsInstance(entry["number"], int)
                self.assertIsInstance(entry["summary"], str)
                self.assertTrue(entry["summary"])
                self.assertTrue(callable(entry["handler"]))
                schema = entry["schema"]
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)
                self.assertIsInstance(schema.get("required"), list)
                for name, prop in schema["properties"].items():
                    self.assertIsInstance(prop, dict, (skill_id, name))
                    self.assertTrue("type" in prop or "enum" in prop,
                                    (skill_id, name))
                fields = schema["properties"].get("fields")
                if fields:
                    self.assertIs(fields["items"]["additionalProperties"], False)

    def test_handler_result_shape_and_defaults(self):
        for skill_id, result in all_writes():
            self.assertEqual(set(result), {"data", "writes", "limitations"},
                             skill_id)
            self.assertIsInstance(result["data"], dict)
            self.assertIsInstance(result["limitations"], list)
            self.assertTrue(result["writes"], skill_id)
            for write in result["writes"]:
                self.assertEqual(set(write), {"path", "content"})
                self.assertIsInstance(write["content"], str)
                self.assertTrue(write["path"].startswith("demo-app/"), skill_id)
                self.assertNotIn("..", write["path"], skill_id)

    def test_requirements_pinned(self):
        result = backend_api.SKILLS["be.gen.api.crud"]["handler"]({})
        reqs = [w for w in result["writes"]
                if w["path"].endswith("requirements.txt")]
        self.assertEqual(len(reqs), 1)
        for pin in EXPECTED_REQUIREMENTS:
            self.assertIn(pin, reqs[0]["content"])

    def test_run_skills_return_recipe_not_execution(self):
        for skill_id in ("db.run.migration", "db.run.seed"):
            result = database.SKILLS[skill_id]["handler"]({})
            self.assertIs(result["data"]["executed"], False, skill_id)
            self.assertEqual(result["data"]["status"], "recipe_ready")
            self.assertIn("recipe", result["data"])
            joined = " ".join(result["limitations"]).lower()
            self.assertIn("root", joined, skill_id)


# ---------------------------------------------------------------------------
# generated-source static checks
# ---------------------------------------------------------------------------

class TestGeneratedSource(unittest.TestCase):
    def test_every_generated_python_compiles(self):
        count = 0
        for skill_id, result in all_writes():
            for write in result["writes"]:
                if write["path"].endswith(".py"):
                    compile(write["content"], write["path"], "exec")
                    count += 1
        self.assertGreater(count, 20)

    def test_custom_args_compile(self):
        args = {"project": "toko-maju", "entity": "order_item",
                "fields": [{"name": "ref_code", "type": "str", "max": 40,
                            "required": True, "unique": True},
                           {"name": "amount", "type": "float"},
                           {"name": "due_date", "type": "date", "required": False},
                           {"name": "contact", "type": "email"},
                           {"name": "paid", "type": "bool", "required": False},
                           {"name": "visits", "type": "int", "required": False},
                           {"name": "memo", "type": "text", "required": False}]}
        for skill_id, result in all_writes(args):
            for write in result["writes"]:
                if write["path"].endswith(".py"):
                    compile(write["content"], skill_id + ":" + write["path"], "exec")
                self.assertTrue(write["path"].startswith("toko-maju/"), skill_id)
        # optional fields must not render invalid "Field(...) = None" shapes
        crud = backend_api.SKILLS["be.gen.api.crud"]["handler"](args)
        src = crud["writes"][0]["content"]
        self.assertIn("Optional[str] = Field(default=None", src)
        self.assertIn("Optional[int] = Field(default=None", src)
        import re
        self.assertIsNone(re.search(r"Field\([^)]*\)\s*=\s*None", src))

    def test_no_js_and_no_todo_stubs(self):
        for skill_id, result in all_writes():
            for write in result["writes"]:
                self.assertFalse(write["path"].endswith((".js", ".ts")),
                                 "backend worker emits no JS: %s" % skill_id)
                self.assertNotIn("TODO", write["content"], skill_id)

    def test_sql_is_parameterized_not_interpolated(self):
        for skill_id in ("be.gen.api.crud", "be.gen.orm.model"):
            result = backend_api.SKILLS[skill_id]["handler"]({})
            src = result["writes"][0]["content"]
            self.assertIn("?", src)
            for line in src.splitlines():
                if any(k in line for k in ("SELECT ", "INSERT INTO", "UPDATE ",
                                           "DELETE FROM")):
                    self.assertNotIn('f"', line, (skill_id, line))

    def test_auth_source_hardening(self):
        result = backend_api.SKILLS["be.gen.auth.jwt"]["handler"]({})
        src = result["writes"][0]["content"]
        self.assertIn('algorithms=["HS256"]', src)
        self.assertIn('"require"', src)
        self.assertIn("pbkdf2_hmac", src)
        self.assertIn("auth_secret_required", src)
        self.assertIn("refresh_reuse_detected", src)
        self.assertIn('os.environ.get("APP_AUTH_SECRET"', src)
        self.assertNotIn('SECRET = "', src)
        oauth = [w for w in result["writes"]
                 if w["path"].endswith("oauth_providers.py")][0]["content"]
        for allowed in ("https://accounts.google.com/o/oauth2/v2/auth",
                        "https://github.com/login/oauth/authorize"):
            self.assertIn(allowed, oauth)
        self.assertNotIn("http://", oauth)


# ---------------------------------------------------------------------------
# database artifact roundtrips (SQLite executed for real)
# ---------------------------------------------------------------------------

class TestDatabaseRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hmk-db-")
        purge_app_modules()

    def tearDown(self):
        purge_app_modules()

    def _sqlite(self, name):
        return os.path.join(self.tmp, name)

    def _apply(self, skill_id, args=None, pick="sqlite.sql", subdir=None):
        result = database.SKILLS[skill_id]["handler"](args or {})
        content = [w for w in result["writes"] if w["path"].endswith(pick)][0]
        return content["content"], result

    def test_schema_and_seed_roundtrip(self):
        schema, _ = self._apply("db.gen.schema")
        seed, _ = self._apply("db.gen.seed")
        db = self._sqlite("round.sqlite")
        conn = sqlite3.connect(db)
        conn.executescript(schema)
        conn.executescript(seed)
        conn.commit()
        rows = conn.execute("SELECT title, price, stock FROM products "
                            "ORDER BY id").fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0][0], "Sample title 01")
        self.assertEqual(rows[0][1], 10)
        self.assertEqual(rows[0][2], 10)
        conn.close()

    def test_seed_is_idempotent_with_unique_field(self):
        schema, _ = self._apply("db.gen.schema")
        seed, _ = self._apply("db.gen.seed")
        conn = sqlite3.connect(self._sqlite("idem.sqlite"))
        conn.executescript(schema)
        conn.executescript(seed)
        conn.executescript(seed)
        conn.commit()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 5)
        conn.close()

    def test_migration_runner_roundtrip_and_ledger(self):
        result = database.SKILLS["db.run.migration"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "proj1"))
        db = self._sqlite("mig.sqlite")
        sqlite3.connect(db).close()  # create empty database file
        runner = os.path.join(root, "demo-app", "database", "run",
                              "apply_migration.py")
        migrations = os.path.join(root, "demo-app", "database", "migrations")
        first = run_py(runner, db, migrations)
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual(len(report["applied"]), 1)
        self.assertTrue(os.path.exists(report["backup"]))
        conn = sqlite3.connect(db)
        self.assertIsNotNone(conn.execute(
            "SELECT name FROM sqlite_master WHERE name='products'").fetchone())
        conn.close()
        again = run_py(runner, db, migrations)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["applied"], [])
        self.assertEqual(len(json.loads(again.stdout)["skipped"]), 1)
        refused = run_py(runner, db, migrations, "--down",
                         report["applied"][0].replace(".up.sql", ""))
        self.assertEqual(refused.returncode, 2)
        self.assertIn("refused", refused.stdout)

    def test_migration_preserves_data_and_adds_column(self):
        runner_result = database.SKILLS["db.run.migration"]["handler"]({})
        second = database.SKILLS["db.gen.migration"]["handler"]({
            "change": "add_columns", "version": 2,
            "fields": [{"name": "sku", "type": "str", "required": False}]})
        root = materialize(runner_result["writes"], os.path.join(self.tmp, "proj2"))
        db = self._sqlite("mig2.sqlite")
        sqlite3.connect(db).close()
        runner = os.path.join(root, "demo-app", "database", "run",
                              "apply_migration.py")
        migrations = os.path.join(root, "demo-app", "database", "migrations")
        out = run_py(runner, db, migrations)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["applied"],
                         ["0001_create_table_products.up.sql"])
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO products (title, price) VALUES ('keep', 7)")
        conn.commit()
        conn.close()
        # only now does the 0002 migration arrive
        materialize(second["writes"], root)
        out = run_py(runner, db, migrations)
        self.assertEqual(out.returncode, 0, out.stderr)
        applied = json.loads(out.stdout)["applied"]
        self.assertEqual(applied, ["0002_add_columns_products.up.sql"])
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT title, price, sku FROM products").fetchone()
        self.assertEqual(row[0], "keep")
        self.assertEqual(row[1], 7)
        self.assertIsNone(row[2])
        conn.close()

    def test_seed_runner_roundtrip(self):
        result = database.SKILLS["db.run.seed"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "proj3"))
        schema, _ = self._apply("db.gen.schema")
        db = self._sqlite("seed.sqlite")
        conn = sqlite3.connect(db)
        conn.executescript(schema)
        conn.commit()
        conn.close()
        runner = os.path.join(root, "demo-app", "database", "run", "apply_seed.py")
        seed_dir = os.path.join(root, "demo-app", "database", "seed")
        out = run_py(runner, db, seed_dir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["rows_affected"], 5)
        out2 = run_py(runner, db, seed_dir)
        self.assertEqual(json.loads(out2.stdout)["rows_affected"], 0)
        conn = sqlite3.connect(db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 5)
        conn.close()

    def test_indexes_used_by_query_plan(self):
        schema, _ = self._apply("db.gen.schema")
        indexes, _ = self._apply("db.gen.indexes")
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema)
        conn.executescript(indexes)
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM products WHERE price = 5"
        ).fetchall()
        self.assertTrue(any("USING INDEX idx_products_price" in str(r) for r in plan),
                        plan)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_products_price", names)
        conn.close()

    def test_relations_foreign_keys_enforced(self):
        schema_prod, _ = self._apply("db.gen.schema")
        schema_cat, _ = self._apply("db.gen.schema", {"entity": "category",
                                                      "fields": [
            {"name": "name", "type": "str", "required": True, "unique": True}]})
        rel_result = database.SKILLS["db.gen.relations"]["handler"]({
            "on_delete": "cascade"})
        relations = [w for w in rel_result["writes"]
                     if w["path"].endswith("sqlite.sql")][0]["content"]
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema_cat)
        conn.executescript(schema_prod)
        conn.executescript(relations)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO categories (name) VALUES ('cat')")
        conn.execute("INSERT INTO products (title, price, category_id) "
                     "VALUES ('p', 1, 1)")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO products (title, price, category_id) "
                         "VALUES ('orphan', 1, 999)")
        conn.commit()
        conn.execute("DELETE FROM categories WHERE id = 1")
        conn.commit()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)
        conn.close()

    def test_relations_many_to_many_join(self):
        schema_prod, _ = self._apply("db.gen.schema")
        schema_tag, _ = self._apply("db.gen.schema", {"entity": "tag",
                                                      "fields": [
            {"name": "label", "type": "str", "required": True, "unique": True}]})
        rel_result = database.SKILLS["db.gen.relations"]["handler"]({
            "many_to_many": True})
        relations = [w for w in rel_result["writes"]
                     if w["path"].endswith("sqlite.sql")][0]["content"]
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema_prod)
        conn.executescript(schema_tag)
        conn.executescript(relations)
        conn.execute("PRAGMA foreign_keys=ON")
        join_cols = {r[1] for r in conn.execute("PRAGMA table_info(products_tags)")}
        self.assertIn("product_id", join_cols)
        self.assertIn("tag_id", join_cols)
        conn.execute("INSERT INTO products (title, price) VALUES ('p', 1)")
        conn.execute("INSERT INTO tags (label) VALUES ('new')")
        conn.execute("INSERT INTO products_tags (product_id, tag_id) VALUES (1, 1)")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO products_tags (product_id, tag_id) "
                         "VALUES (99, 1)")
        conn.close()

    def test_triggers_write_audit_rows(self):
        schema, _ = self._apply("db.gen.schema")
        triggers, _ = self._apply("db.gen.triggers")
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema)
        conn.executescript(triggers)
        conn.execute("INSERT INTO products (title, price) VALUES ('a', 1)")
        conn.execute("UPDATE products SET price = 2 WHERE id = 1")
        conn.execute("DELETE FROM products WHERE id = 1")
        conn.commit()
        ops = [r[0] for r in conn.execute(
            "SELECT op FROM products_audit ORDER BY audit_id")]
        self.assertEqual(ops, ["INSERT", "UPDATE", "DELETE"])
        conn.close()

    def test_stored_proc_sqlite_guarded_equivalent(self):
        schema, _ = self._apply("db.gen.schema")
        seed, _ = self._apply("db.gen.seed")
        proc, _ = self._apply("db.gen.stored_proc")
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema)
        conn.executescript(seed)
        conn.commit()
        import re
        match = re.search(r"^UPDATE .*?;", proc, re.S | re.M)
        guarded = " ".join(match.group(0).split())
        self.assertIn("WHERE id = :id", guarded)
        column = re.search(r"SET (\w+) =", guarded).group(1)
        cur = conn.execute(guarded, {"id": 1, "qty": 5})
        self.assertEqual(cur.rowcount, 1)
        cur = conn.execute(guarded, {"id": 1, "qty": 100000})
        self.assertEqual(cur.rowcount, 0)
        conn.commit()
        stock = conn.execute("SELECT %s FROM products WHERE id = 1" % column).fetchone()[0]
        self.assertEqual(stock, 5)
        conn.close()

    def test_backup_runner_retention_and_verify(self):
        result = database.SKILLS["db.backup.automate"]["handler"]({"keep": 2})
        root = materialize(result["writes"], os.path.join(self.tmp, "proj4"))
        schema, _ = self._apply("db.gen.schema")
        seed, _ = self._apply("db.gen.seed")
        db = self._sqlite("live.sqlite")
        conn = sqlite3.connect(db)
        conn.executescript(schema)
        conn.executescript(seed)
        conn.commit()
        conn.close()
        runner = os.path.join(root, "demo-app", "database", "run", "backup.py")
        backups = []
        for _ in range(3):
            out = run_py(runner, db, "--out", self._sqlite("backups"), "--keep", "2")
            self.assertEqual(out.returncode, 0, out.stderr)
            backups.append(json.loads(out.stdout)["backup"])
            time.sleep(1.05)
        remaining = sorted(f for f in os.listdir(self._sqlite("backups"))
                           if f.endswith(".sqlite"))
        self.assertEqual(len(remaining), 2)
        conn = sqlite3.connect(os.path.join(self._sqlite("backups"), remaining[-1]))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 5)
        conn.close()
        self.assertTrue(os.path.exists(db), "live database must never be pruned")


# ---------------------------------------------------------------------------
# generated backend modules executed for real (stdlib-only parts)
# ---------------------------------------------------------------------------

class TestGeneratedBackendExecutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hmk-be-")
        purge_app_modules()

    def tearDown(self):
        purge_app_modules()

    def _repo_and_service(self):
        repo = backend_api.SKILLS["be.gen.orm.model"]["handler"]({})
        service = backend_api.SKILLS["be.gen.service"]["handler"]({})
        root = materialize(repo["writes"] + service["writes"],
                           os.path.join(self.tmp, "proj"))
        backend_dir = os.path.join(root, "demo-app", "backend")
        sys.path.insert(0, backend_dir)
        self.addCleanup(sys.path.remove, backend_dir)
        return backend_dir

    def test_repository_crud_roundtrip(self):
        self._repo_and_service()
        with Env(APP_DATABASE=self._dbpath("repo.sqlite")):
            repo = importlib.import_module("app.repositories.product_repo")
            with repo.connect() as conn:
                created = repo.create(conn, repo.Product(id=None, title="Alpha",
                                                         price=10))
                self.assertEqual(created.id, 1)
                self.assertIsNotNone(created.created_at)
                fetched = repo.get(conn, 1)
                self.assertEqual(fetched.title, "Alpha")
                listed = repo.list_records(conn, limit=1000)
                self.assertEqual(len(listed), 1)
                self.assertTrue(repo.update(conn, 1, {"price": 12}))
                self.assertFalse(repo.update(conn, 1, {}))
                with self.assertRaises(ValueError):
                    repo.update(conn, 1, {"evil; DROP": 1})
                with self.assertRaises(sqlite3.IntegrityError):
                    repo.create(conn, repo.Product(id=None, title="Alpha", price=1))
                self.assertTrue(repo.delete(conn, 1))
                self.assertIsNone(repo.get(conn, 1))

    def _dbpath(self, name):
        return os.path.join(self.tmp, name)

    def test_service_business_rules(self):
        self._repo_and_service()
        with Env(APP_DATABASE=self._dbpath("svc.sqlite")):
            svc = importlib.import_module("app.services.product_service")
            item = svc.create_entity({"title": "Beta", "price": 5})
            self.assertEqual(item.title, "beta")
            with self.assertRaises(svc.BusinessError):
                svc.create_entity({"title": "Beta", "price": 5})
            with self.assertRaises(svc.BusinessError):
                svc.create_entity({"title": "  ", "price": 5})
            with self.assertRaises(svc.BusinessError):
                svc.create_entity({"title": "ok", "price": 5, "evil": 1})
            with self.assertRaises(svc.BusinessError):
                svc.create_entity({"price": 5})
            svc.create_entity({"title": "Gamma", "price": 6})
            self.assertEqual(len(svc.list_entities(limit=1000)), 2)
            updated = svc.update_entity(1, {"price": 50})
            self.assertEqual(updated.price, 50)
            with self.assertRaises(svc.BusinessError):
                svc.update_entity(999, {"price": 1})
            svc.delete_entity(2)
            with self.assertRaises(svc.BusinessError):
                svc.delete_entity(2)

    def test_queue_retry_backoff_and_dead_letter(self):
        with Env(APP_DATABASE=self._dbpath("queue.sqlite")):
            queue = load_module(self._write_queue(), "gen_task_queue")
        def boom(payload):
            raise RuntimeError("nope")
        queue.HANDLERS["demo.fail"] = boom
        task_id = queue.enqueue("demo.fail", {"x": 1}, max_attempts=2)
        first = queue.worker_once("demo.fail")
        self.assertEqual(first["status"], "retry")
        self.assertEqual(first["attempt"], 1)
        conn = sqlite3.connect(queue.DATABASE)
        run_at = conn.execute("SELECT run_at FROM tasks WHERE id = ?",
                              (task_id,)).fetchone()[0]
        self.assertGreater(run_at, time.time())
        conn.execute("UPDATE tasks SET run_at = 0 WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        second = queue.worker_once("demo.fail")
        self.assertEqual(second["status"], "dead")
        depth = queue.queue_depth("demo.fail")
        self.assertEqual((depth["dead"], depth["queued"]), (1, 0))
        queue.enqueue("example.sum", {"a": 2, "b": 3})
        done = queue.worker_once("example.sum")
        self.assertEqual((done["status"], done["result"]), ("done", {"sum": 5}))
        self.assertIsNone(queue.worker_once("example.sum"))

    def _write_queue(self):
        result = backend_api.SKILLS["be.gen.queue"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "q"))
        return os.path.join(root, "demo-app", "backend", "app", "task_queue.py")

    def test_cron_due_scheduling_and_failure_isolation(self):
        cron = load_module(self._write_cron(), "gen_cron")
        calls = []
        def job():
            calls.append(1)
            return {"done": True}
        def broken():
            raise RuntimeError("boom")
        registry = [{"name": "j", "every_s": 60, "fn": job},
                    {"name": "bad", "every_s": 60, "fn": broken}]
        out = cron.run_due(now=1000.0, registry=registry)
        self.assertTrue(out[0]["ok"] and out[1]["ok"] is False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cron.run_due(now=1010.0, registry=registry), [])
        out2 = cron.run_due(now=1061.0, registry=registry)
        self.assertTrue(all(e["ok"] or e["job"] == "bad" for e in out2))
        self.assertEqual(len(calls), 2)

    def _write_cron(self):
        result = backend_api.SKILLS["be.gen.cron"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "c"))
        return os.path.join(root, "demo-app", "backend", "app", "cron_jobs.py")

    def test_upload_core_bounds_and_safety(self):
        with Env(APP_UPLOAD_MAX_BYTES="2048"):
            upload = load_module(self._write_upload(), "gen_upload_core")
        good_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        saved = upload.save_upload("photo.png", good_png,
                                   directory=self._dbpath("uploads"))
        self.assertTrue(saved["stored"].endswith(".png"))
        self.assertGreater(len(saved["stored"]), 20)
        stored_path = os.path.join(self._dbpath("uploads"), saved["stored"])
        self.assertTrue(os.path.exists(stored_path))
        with self.assertRaises(upload.UploadError):
            upload.save_upload("../../etc/passwd.png", good_png,
                               directory=self._dbpath("uploads"))
        with self.assertRaises(upload.UploadError):
            upload.save_upload("evil.exe", good_png, directory=self._dbpath("uploads"))
        with self.assertRaises(upload.UploadError):
            upload.save_upload("fake.png", b"MZ not a png" + b"0" * 16,
                               directory=self._dbpath("uploads"))
        with self.assertRaises(upload.UploadError):
            upload.save_upload("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * 4096,
                               directory=self._dbpath("uploads"))

    def _write_upload(self):
        result = backend_api.SKILLS["be.gen.upload"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "u"))
        return os.path.join(root, "demo-app", "backend", "app", "upload_core.py")

    def test_upload_fail_closed_without_dir(self):
        with Env(APP_UPLOAD_DIR=None):
            upload = load_module(self._write_upload2(), "gen_upload_core2")
            with self.assertRaises(upload.UploadError):
                upload.check_config()

    def _write_upload2(self):
        result = backend_api.SKILLS["be.gen.upload"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "u2"))
        return os.path.join(root, "demo-app", "backend", "app", "upload_core.py")

    def test_payment_signatures_and_trust_policy(self):
        with Env(APP_DATABASE=self._dbpath("pay.sqlite")):
            pay = load_module(self._write_payment(), "gen_payment_core")
            with self.assertRaises(pay.PaymentConfigError):
                pay.require_server_key("APP_MIDTRANS_SERVER_KEY")
            server_key = "SB_Mid_server_secret_key_123"
            with Env(APP_MIDTRANS_SERVER_KEY=server_key):
                intent = pay.create_intent("pi_1", 150000.00, "idr")
                self.assertEqual(intent["amount_minor"], 15000000)
                self.assertEqual(intent["status"], "pending")
                with self.assertRaises(ValueError):
                    pay.amount_minor(0.01, "idr")
                with self.assertRaises(ValueError):
                    pay.create_intent("pi_bad", 10**10, "idr")
                good = hashlib.sha512(
                    ("pi_1" + "200" + "15000000.00" + server_key).encode()).hexdigest()
                self.assertTrue(pay.verify_midtrans(
                    {"order_id": "pi_1", "status_code": "200",
                     "gross_amount": "15000000.00", "signature_key": good},
                    server_key))
                self.assertFalse(pay.verify_midtrans(
                    {"order_id": "pi_1", "status_code": "200",
                     "gross_amount": "15000000.00", "signature_key": "bad"},
                    server_key))
                payload = b'{"id":"evt_1","data":{"object":{"id":"pi_1","amount":15000000}}}'
                secret = "whsec_stripe_test_secret_000"
                ts = int(time.time())
                sig = hmac.new(secret.encode(), ("%d." % ts).encode() + payload,
                               hashlib.sha256).hexdigest()
                self.assertTrue(pay.verify_stripe(
                    "t=%d,v1=%s" % (ts, sig), payload, secret))
                self.assertFalse(pay.verify_stripe(
                    "t=%d,v1=%s" % (ts - 4000, sig), payload, secret))
                outcome = pay.apply_webhook("midtrans", "evt-m-1", "pi_1",
                                            15000000, True)
                self.assertFalse(outcome["paid"])
                self.assertEqual(outcome["status"], "webhook_verified_pending_review")
                outcome = pay.apply_webhook("midtrans", "evt-m-2", "pi_1",
                                            15000000, True)
                self.assertTrue(outcome["recorded"])
                with Env(APP_PAYMENT_TRUST_WEBHOOK="true"):
                    outcome = pay.apply_webhook("midtrans", "evt-m-3", "pi_1",
                                                15000000, True)
                    self.assertFalse(outcome["paid"])
                outcome = pay.apply_webhook("midtrans", "evt-m-3", "pi_1",
                                            15000000, True)
                self.assertEqual(outcome["reason"], "duplicate_event")
                outcome = pay.apply_webhook("midtrans", "evt-m-4", "pi_1",
                                            999, True)
                self.assertEqual(outcome["reason"], "amount_mismatch")
                outcome = pay.apply_webhook("midtrans", "evt-m-5", "pi_x",
                                            100, True)
                self.assertEqual(outcome["reason"], "unknown_intent")

    def test_signed_webhook_never_proves_paid_even_with_trust_flag(self):
        path = self._write_payment()
        with Env(APP_DATABASE=os.path.join(self.tmp, "reconcile.sqlite"), APP_PAYMENT_TRUST_WEBHOOK="true"):
            pay = load_module(path, "payment_reconciliation_regression")
            pay.create_intent("order-123", 150000, "idr")
            for status in ("pending", "settlement"):
                result = pay.apply_webhook("midtrans", "order-123:"+status, "order-123", 15000000, True)
                self.assertTrue(result["recorded"])
                self.assertFalse(result["paid"])
            self.assertTrue(pay.apply_webhook("stripe", "order-123:pending", "order-123", 15000000, True)["recorded"])

    def _write_payment(self):
        result = backend_api.SKILLS["be.gen.payment"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "p"))
        return os.path.join(root, "demo-app", "backend", "app", "payment_core.py")

    def test_oauth_allowlist_pkce_and_state(self):
        oauth = load_module(self._write_oauth(), "gen_oauth")
        self.assertEqual(sorted(oauth.PROVIDERS), ["github", "google"])
        with self.assertRaises(ValueError):
            oauth.get_provider("facebook")
        with self.assertRaises(ValueError):
            oauth.get_provider("https://evil.example/authorize")
        with Env(APP_OAUTH_GOOGLE_CLIENT_ID=None, APP_OAUTH_GOOGLE_CLIENT_SECRET=None):
            with self.assertRaises(RuntimeError):
                oauth.client_config(oauth.PROVIDERS["google"], "APP_OAUTH_GOOGLE")
        verifier, challenge = oauth.pkce_pair()
        self.assertNotIn("=", verifier)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(challenge, expected)
        state = oauth.new_state()
        sig = oauth.sign_state(state, "s" * 40)
        self.assertTrue(oauth.verify_state(state, sig, "s" * 40,
                                           time.time() - 10, time.time()))
        self.assertFalse(oauth.verify_state(state, "tampered", "s" * 40,
                                            time.time() - 10, time.time()))
        self.assertFalse(oauth.verify_state(state, sig, "s" * 40,
                                            time.time() - 601, time.time()))

    def _write_oauth(self):
        result = backend_api.SKILLS["be.gen.auth.oauth"]["handler"]({})
        root = materialize(result["writes"], os.path.join(self.tmp, "o"))
        return os.path.join(root, "demo-app", "backend", "app", "oauth_providers.py")


# ---------------------------------------------------------------------------
# JWT auth lifecycle (PyJWT shim) + fail-closed secrets
# ---------------------------------------------------------------------------

class TestAuthJwt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hmk-auth-")
        result = backend_api.SKILLS["be.gen.auth.jwt"]["handler"]({})
        self.auth_src = result["writes"][0]["content"]
        purge_app_modules()

    def tearDown(self):
        purge_app_modules()

    def _env(self, db):
        return {"APP_AUTH_SECRET": "unit-test-signing-secret-0123456789abcdef",
                "APP_DATABASE": db}

    def test_fail_closed_without_secret(self):
        ns = exec_auth_source(self.auth_src, "auth_nosecret",
                              {"APP_AUTH_SECRET": None,
                               "APP_DATABASE": os.path.join(self.tmp, "x.sqlite")})
        with self.assertRaises(RuntimeError):
            ns["require_secret"]()
        with self.assertRaises(RuntimeError):
            ns["login"]("a@b.co", "password-password")
        with self.assertRaises(RuntimeError):
            ns["decode_access"]("whatever")

    def test_full_lifecycle_rotation_and_revocation(self):
        db = os.path.join(self.tmp, "auth.sqlite")
        ns = exec_auth_source(self.auth_src, "auth_main", self._env(db))
        register, login = ns["register"], ns["login"]
        decode, refresh, logout = ns["decode_access"], ns["refresh"], ns["logout"]
        AuthError = ns["AuthError"]
        user_id = register("User@Example.com", "Sup3rSecret!pass")
        self.assertIsInstance(user_id, int)
        with self.assertRaises(ValueError):
            register("User@Example.com", "Sup3rSecret!pass2")
        with self.assertRaises(ValueError):
            register("not-an-email", "Sup3rSecret!pass")
        with self.assertRaises(ValueError):
            register("short@x.io", "short")
        with self.assertRaises(AuthError):
            login("user@example.com", "WrongPassword!123")
        tokens = login("user@example.com", "Sup3rSecret!pass")
        payload = decode(tokens["access_token"])
        self.assertEqual(payload["sub"], str(user_id))
        self.assertEqual(payload["typ"], "access")
        shim = make_jwt_shim()
        expired = shim.encode({"sub": "1", "iat": int(time.time()) - 500,
                               "exp": int(time.time()) - 100, "typ": "access"},
                              self._env(db)["APP_AUTH_SECRET"], algorithm="HS256")
        with self.assertRaises(AuthError):
            decode(expired)
        tampered = tokens["access_token"][:-3] + ("aaa" if not
                                                  tokens["access_token"].endswith("aaa") else "bbb")
        with self.assertRaises(AuthError):
            decode(tampered)
        rotated = refresh(tokens["refresh_token"])
        self.assertNotEqual(rotated["refresh_token"], tokens["refresh_token"])
        with self.assertRaises(AuthError):
            refresh(tokens["refresh_token"])
        with self.assertRaises(AuthError):
            refresh(rotated["refresh_token"])
        tokens2 = login("user@example.com", "Sup3rSecret!pass")
        logout(tokens2["refresh_token"])
        with self.assertRaises(AuthError):
            refresh(tokens2["refresh_token"])
        with self.assertRaises(AuthError):
            decode("garbage.token.here")

    def test_refresh_expiry(self):
        db = os.path.join(self.tmp, "auth2.sqlite")
        ns = exec_auth_source(self.auth_src, "auth_exp", self._env(db))
        ns["register"]("u2@example.com", "Sup3rSecret!pass")
        tokens = ns["login"]("u2@example.com", "Sup3rSecret!pass")
        import sqlite3 as s3
        conn = s3.connect(db)
        conn.execute("UPDATE refresh_tokens SET expires = ? WHERE 1=1",
                     (time.time() - 10,))
        conn.commit()
        conn.close()
        with self.assertRaises(ns["AuthError"]):
            ns["refresh"](tokens["refresh_token"])


# ---------------------------------------------------------------------------
# security hardening of generation inputs
# ---------------------------------------------------------------------------

class TestSecurityHardening(unittest.TestCase):
    def test_injection_in_field_name_is_neutralized(self):
        hostile = "x'); DROP TABLE products;--"
        result = backend_api.SKILLS["be.gen.orm.model"]["handler"]({
            "fields": [{"name": hostile, "type": "str", "required": True}]})
        src = result["writes"][0]["content"]
        self.assertNotIn("DROP TABLE", src)
        schema = database.SKILLS["db.gen.schema"]["handler"]({
            "fields": [{"name": hostile, "type": "str", "required": True}]})
        ddl = [w for w in schema["writes"] if w["path"].endswith("sqlite.sql")][0]
        conn = sqlite3.connect(":memory:")
        conn.executescript(ddl["content"])
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("products", names)
        conn.close()

    def test_injection_in_seed_row_is_escaped(self):
        hostile = "Robert'); DROP TABLE products;--"
        result = database.SKILLS["db.gen.seed"]["handler"]({
            "rows": [[hostile, 1, None]]})
        seed = [w for w in result["writes"]
                if w["path"].endswith("sqlite.sql")][0]["content"]
        schema, _ = self._apply("db.gen.schema")
        conn = sqlite3.connect(":memory:")
        conn.executescript(schema)
        conn.executescript(seed)
        rows = conn.execute("SELECT title FROM products").fetchall()
        self.assertEqual(rows, [(hostile,)])
        conn.close()

    def _apply(self, skill_id, args=None, pick="sqlite.sql"):
        result = database.SKILLS[skill_id]["handler"](args or {})
        return [w for w in result["writes"] if w["path"].endswith(pick)][0]["content"], result

    def test_hostile_slugs_rejected(self):
        for bad in ("../evil", "..", "/abs", "Demo App", "demo_app", "dist",
                    "a", "x" * 51, "demo--app"):
            for module in (backend_api, database):
                skill = module.SKILLS[next(iter(module.SKILLS))]
                with self.assertRaises(Exception, msg=bad):
                    skill["handler"]({"project": bad})

    def test_hostile_field_types_rejected(self):
        with self.assertRaises(Exception):
            backend_api.SKILLS["be.gen.api.crud"]["handler"]({
                "fields": [{"name": "ok", "type": "text; DROP"}]})
        with self.assertRaises(Exception):
            backend_api.SKILLS["be.gen.api.crud"]["handler"]({
                "fields": [{"name": "created_at", "type": "str"}]})
        with self.assertRaises(Exception):
            database.SKILLS["db.gen.stored_proc"]["handler"]({
                "fields": [{"name": "title", "type": "str"}]})

    def test_payment_never_claims_money_moved(self):
        result = backend_api.SKILLS["be.gen.payment"]["handler"]({})
        core = result["writes"][0]["content"]
        self.assertIn("NEVER claims money was processed", core)
        self.assertIn("APP_PAYMENT_TRUST_WEBHOOK", core)
        joined = " ".join(result["limitations"]).lower()
        self.assertIn("no fake integration", joined)
        for write in result["writes"]:
            self.assertNotIn("status_code=200, 'paid'", write["content"])

    def test_db_run_handlers_never_execute(self):
        for skill_id in ("db.run.migration", "db.run.seed"):
            result = database.SKILLS[skill_id]["handler"]({})
            self.assertIs(result["data"]["executed"], False)
            for write in result["writes"]:
                self.assertTrue(write["path"].endswith((".sql", ".py")))
            runner = [w for w in result["writes"]
                      if w["path"].endswith(".py")][0]["content"]
            compile(runner, skill_id + "-runner", "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
