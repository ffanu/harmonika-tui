"""Database generation skills (IDs 87-96) for the Harmonika web builder.

Generates real dialect source: executable SQLite DDL/seed/migrations plus
PostgreSQL and MySQL variants. Every identifier passes through
``safe.sql_ident`` and every value through ``safe.sql_value`` (escaped, never
string-interpolated from unvalidated input). Handlers only return write
manifests; the root executor writes files atomically and runs ``db.run.*``
inside ITS sandbox - skills 90/91 therefore return recipes with
``executed: False`` and never execute anything themselves.

Scope (catalog numbers):
  87 db.gen.schema       91 db.run.seed        95 db.gen.stored_proc
  88 db.gen.migration    92 db.gen.indexes     96 db.backup.automate
  89 db.gen.seed         93 db.gen.relations
  90 db.run.migration    94 db.gen.triggers
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from . import safe
except ImportError:  # pragma: no cover - loaded outside the package
    import safe

FIELD_TYPES = ("str", "text", "int", "float", "bool", "datetime", "date", "email")
RESERVED_COLUMNS = frozenset({"id", "created_at", "updated_at"})

SQLITE_TYPE = {"str": "TEXT", "text": "TEXT", "int": "INTEGER", "float": "REAL",
               "bool": "INTEGER", "datetime": "TEXT", "date": "TEXT", "email": "TEXT"}
PG_TYPE = {"str": "VARCHAR({max})", "text": "TEXT", "int": "BIGINT",
           "float": "DOUBLE PRECISION", "bool": "BOOLEAN", "datetime": "TIMESTAMPTZ",
           "date": "DATE", "email": "VARCHAR(254)"}
MYSQL_TYPE = {"str": "VARCHAR({max})", "text": "TEXT", "int": "BIGINT",
              "float": "DOUBLE", "bool": "TINYINT(1)", "datetime": "DATETIME",
              "date": "DATE", "email": "VARCHAR(254)"}

DEFAULT_FIELDS = [
    {"name": "title", "type": "str", "required": True, "unique": True, "max": 120},
    {"name": "price", "type": "int", "required": True},
    {"name": "stock", "type": "int", "required": False},
]

EXEC_NOTE = ("db.run.* skills never execute anything: the root executor runs the "
             "recipe inside its sandbox; this handler only returns the recipe and "
             "the runner script.")


# ---------------------------------------------------------------------------
# argument conventions (same contract as backend_api.py)
# ---------------------------------------------------------------------------

def _plural(word: str) -> str:
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def _project(args: Dict[str, Any]) -> str:
    return safe.slug(args.get("project") or "demo-app", "project")


def _entity(args: Dict[str, Any], default: str = "product") -> str:
    return safe.snake(args.get("entity") or default, "entity")


def _table(entity: str) -> str:
    return safe.sql_ident(_plural(entity), "table")


def _fields(args: Dict[str, Any], default: Optional[List[dict]] = None) -> List[dict]:
    raw = args.get("fields")
    if raw is None:
        raw = [dict(f) for f in (default if default is not None else DEFAULT_FIELDS)]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 24:
        raise safe.SkillInputError("fields: expected 1..24 field specs")
    out: List[dict] = []
    seen = set()
    for spec in raw:
        if not isinstance(spec, dict):
            raise safe.SkillInputError("fields: each field must be an object")
        name = safe.snake(spec.get("name"), "field name")
        ftype = spec.get("type", "str")
        if ftype not in FIELD_TYPES:
            raise safe.SkillInputError(
                "field %s: type must be one of %s" % (name, ", ".join(FIELD_TYPES)))
        if name in seen:
            raise safe.SkillInputError("fields: duplicate name %s" % name)
        seen.add(name)
        if name in RESERVED_COLUMNS:
            raise safe.SkillInputError("field %s: reserved generated column" % name)
        fmax = 0
        if ftype in ("str", "email", "text"):
            fmax = spec.get("max", 120)
            if not isinstance(fmax, int) or isinstance(fmax, bool) or not 1 <= fmax <= 4000:
                raise safe.SkillInputError("field %s: max must be an integer 1..4000" % name)
        out.append({"name": name, "type": ftype,
                    "required": bool(spec.get("required", True)),
                    "unique": bool(spec.get("unique", False)), "max": fmax})
    return out


def _opt_int(args: Dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise safe.SkillInputError("%s: must be an integer in %d..%d" % (key, lo, hi))
    return value


def _opt_bool(args: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise safe.SkillInputError("%s: must be a boolean" % key)
    return value


def _result(data: dict, pairs: list, limitations: list) -> dict:
    return {"data": data, "writes": safe.writes_from(pairs), "limitations": list(limitations)}


# ---------------------------------------------------------------------------
# dialect DDL builders
# ---------------------------------------------------------------------------

def _dialect_cols(fields: List[dict], dialect: str) -> List[str]:
    if dialect == "sqlite":
        cols = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for f in fields:
            col = "%s %s" % (f["name"], SQLITE_TYPE[f["type"]])
            if f["required"]:
                col += " NOT NULL"
            if f["unique"]:
                col += " UNIQUE"
            cols.append(col)
        cols.append("created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        cols.append("updated_at TEXT")
        return cols
    if dialect == "postgresql":
        cols = ["id BIGSERIAL PRIMARY KEY"]
        for f in fields:
            col = "%s %s" % (f["name"], PG_TYPE[f["type"]].format(max=f["max"] or 120))
            if f["required"]:
                col += " NOT NULL"
            if f["unique"]:
                col += " UNIQUE"
            cols.append(col)
        cols.append("created_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        cols.append("updated_at TIMESTAMPTZ")
        return cols
    cols = ["id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY"]
    for f in fields:
        col = "%s %s" % (f["name"], MYSQL_TYPE[f["type"]].format(max=f["max"] or 120))
        if f["required"]:
            col += " NOT NULL"
        if f["unique"]:
            col += " UNIQUE"
        cols.append(col)
    cols.append("created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP")
    cols.append("updated_at DATETIME NULL")
    return cols


def _ddl(dialect: str, table: str, fields: List[dict]) -> str:
    cols = ", ".join(_dialect_cols(fields, dialect))
    if dialect == "mysql":
        tail = ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
    else:
        tail = ");"
    return "CREATE TABLE IF NOT EXISTS %s (%s%s" % (table, cols, tail)


def _sample_value(field: dict, index: int) -> Any:
    t = field["type"]
    if t in ("str", "email", "text"):
        return "Sample %s %02d" % (field["name"].replace("_", " "), index)
    if t == "int":
        return index * 10
    if t == "float":
        return round(index * 1.5, 2)
    if t == "bool":
        return index % 2 == 0
    if t == "date":
        return "2026-01-%02d" % ((index % 28) + 1)
    return "2026-01-01T09:%02d:00" % (index % 60)


# ---------------------------------------------------------------------------
# 87 db.gen.schema
# ---------------------------------------------------------------------------

def _gen_schema(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    fields = _fields(args)
    pairs = []
    for dialect in ("sqlite", "postgresql", "mysql"):
        header = "-- %s schema for %s (auto-generated by Harmonika web builder)\n" % (
            dialect, table)
        pairs.append((safe.rel_path(project, "database", "schema", dialect + ".sql"),
                      header + _ddl(dialect, table, fields) + "\n"))
    return _result(
        {"project": project, "table": table, "dialects": ["sqlite", "postgresql", "mysql"],
         "columns": ["id"] + [f["name"] for f in fields] + ["created_at", "updated_at"]},
        pairs,
        ["DDL files are artifacts; apply via an authorized operator or the "
         "db.run.migration recipe (SQLite executes in the root sandbox).",
         "PostgreSQL/MySQL files assume an existing database and a privileged "
         "migration role; they are dialect sources, not executed here."])


# ---------------------------------------------------------------------------
# 88 db.gen.migration
# ---------------------------------------------------------------------------

def _migration_files(project: str, entity: str, table: str, change: str,
                     fields: List[dict], version: int,
                     allow_destructive: bool) -> List[tuple]:
    name = "%04d_%s_%s" % (version, change, table)
    pairs = []
    if change == "create_table":
        bodies = {d: _ddl(d, table, fields) for d in ("sqlite", "postgresql", "mysql")}
    elif change == "add_columns":
        bodies = {}
        for dialect in ("sqlite", "postgresql", "mysql"):
            if dialect == "sqlite":
                stmts = ["ALTER TABLE %s ADD COLUMN %s %s%s;" % (
                    table, f["name"], SQLITE_TYPE[f["type"]],
                    " NOT NULL DEFAULT ''" if f["required"] and f["type"] in
                    ("str", "text", "email") else
                    (" NOT NULL DEFAULT 0" if f["required"] else ""))
                    for f in fields]
            elif dialect == "postgresql":
                stmts = ["ALTER TABLE %s ADD COLUMN %s %s%s;" % (
                    table, f["name"], PG_TYPE[f["type"]].format(max=f["max"] or 120),
                    " NOT NULL DEFAULT ''" if f["required"] and f["type"] in
                    ("str", "text", "email") else
                    (" NOT NULL DEFAULT 0" if f["required"] else ""))
                    for f in fields]
            else:
                stmts = ["ALTER TABLE %s ADD COLUMN %s %s%s;" % (
                    table, f["name"], MYSQL_TYPE[f["type"]].format(max=f["max"] or 120),
                    " NOT NULL DEFAULT ''" if f["required"] and f["type"] in
                    ("str", "text", "email") else
                    (" NOT NULL DEFAULT 0" if f["required"] else ""))
                    for f in fields]
            bodies[dialect] = "\n".join(stmts)
    else:
        raise safe.SkillInputError("change: must be create_table or add_columns")
    for dialect, body in bodies.items():
        header = ("-- migration %s (%s) up; generated by Harmonika web builder\n"
                  "-- transactional: applied atomically by database/run/apply_migration.py\n"
                  % (name, dialect))
        if dialect == "sqlite":
            content = header + "BEGIN IMMEDIATE;\n" + body + "\nCOMMIT;\n"
        elif dialect == "postgresql":
            content = header + "BEGIN;\n" + body + "\nCOMMIT;\n"
        else:
            content = header + "START TRANSACTION;\n" + body + "\nCOMMIT;\n"
        pairs.append((safe.rel_path(project, "database", "migrations", dialect,
                                    name + ".up.sql"), content))
    if allow_destructive:
        # Down migrations only on explicit request; they are data-destructive.
        for dialect in ("sqlite", "postgresql", "mysql"):
            if change == "create_table":
                body = "DROP TABLE IF EXISTS %s;" % table
            else:
                drops = ", ".join("DROP COLUMN " + f["name"] for f in fields)
                body = "ALTER TABLE %s %s;" % (table, drops)
            pairs.append((safe.rel_path(project, "database", "migrations", dialect,
                                        name + ".down.sql"),
                          "-- DESTRUCTIVE down migration %s (%s); review before use\n%s\n"
                          % (name, dialect, body)))
    return pairs


def _gen_migration(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    change = args.get("change", "create_table")
    if change not in ("create_table", "add_columns"):
        raise safe.SkillInputError("change: must be create_table or add_columns")
    version = _opt_int(args, "version", 1, 1, 9999)
    fields = _fields(args)
    allow = _opt_bool(args, "allow_destructive", False)
    pairs = _migration_files(project, entity, table, change, fields, version, allow)
    return _result(
        {"project": project, "table": table, "change": change, "version": version,
         "destructive_down_included": allow,
         "data_safety": "no DROP generated by default; down files only with "
                        "allow_destructive=true"},
        pairs,
        ["Migrations are files; apply via apply_migration.py (db.run.migration "
         "recipe) or an authorized operator.",
         "SQLite ALTER TABLE ADD COLUMN cannot add NOT NULL without a default; "
         "required text/int columns get safe defaults instead."])


# ---------------------------------------------------------------------------
# 89 db.gen.seed
# ---------------------------------------------------------------------------

def _seed_rows(fields: List[dict], count: int,
               custom: Optional[List[list]]) -> List[list]:
    if custom is not None:
        if len(custom) > 50:
            raise safe.SkillInputError("rows: max 50 rows")
        rows = []
        for row in custom:
            if not isinstance(row, list) or len(row) != len(fields):
                raise safe.SkillInputError(
                    "rows: each row must be a list with %d values" % len(fields))
            rows.append([safe.sql_value(v) for v in row])
        return rows
    return [[safe.sql_value(_sample_value(f, i + 1)) for f in fields]
            for i in range(count)]


def _seed_sql(dialect: str, table: str, fields: List[dict],
              rows: List[list]) -> str:
    cols = ", ".join(f["name"] for f in fields)
    values = ",\n  ".join("(" + ", ".join(str(v) for v in row) + ")" for row in rows)
    unique = next((f["name"] for f in fields if f["unique"]), None)
    if dialect == "sqlite":
        verb = "INSERT OR IGNORE INTO" if unique else "INSERT INTO"
    elif dialect == "mysql":
        verb = "INSERT IGNORE INTO" if unique else "INSERT INTO"
    else:
        verb = "INSERT INTO"
    if dialect == "postgresql" and unique:
        body = ("INSERT INTO %s (%s) VALUES\n  %s\nON CONFLICT (%s) DO NOTHING;"
                % (table, cols, values, unique))
    else:
        body = "%s %s (%s) VALUES\n  %s;" % (verb, table, cols, values)
    header = ("-- seed data for %s (%s); generated by Harmonika web builder\n"
              "-- idempotent%s; wrapped in a transaction\n"
              % (table, dialect, " via %s" %
                 ({"sqlite": "INSERT OR IGNORE", "mysql": "INSERT IGNORE",
                   "postgresql": "ON CONFLICT DO NOTHING"}[dialect]) if unique
                 else " only when applied once (no unique field detected)"))
    if dialect == "mysql":
        return header + "START TRANSACTION;\n" + body + "\nCOMMIT;\n"
    return header + "BEGIN;\n" + body + "\nCOMMIT;\n"


def _gen_seed(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    fields = _fields(args)
    count = _opt_int(args, "count", 5, 1, 50)
    custom = args.get("rows")
    if custom is not None and not isinstance(custom, list):
        raise safe.SkillInputError("rows: expected a list of rows")
    rows = _seed_rows(fields, count, custom)
    pairs = [(safe.rel_path(project, "database", "seed", d + ".sql"),
              _seed_sql(d, table, fields, rows))
             for d in ("sqlite", "postgresql", "mysql")]
    return _result(
        {"project": project, "table": table, "rows": len(rows),
         "idempotent": any(f["unique"] for f in fields)},
        pairs,
        ["Seed files are artifacts; apply via apply_seed.py (db.run.seed recipe) "
         "or an authorized operator.",
         "Without a unique field the seed is not idempotent; re-application "
         "would duplicate rows."])


# ---------------------------------------------------------------------------
# 90/91 db.run.migration + db.run.seed (recipes; root sandbox executes)
# ---------------------------------------------------------------------------

MIGRATION_RUNNER = '''#!/usr/bin/env python3
"""Apply pending migration files (auto-generated, stdlib only).

Usage:
    python3 apply_migration.py DB_PATH MIGRATIONS_DIR [--dialect sqlite] [--down NAME]

Safety: integrity check first, automatic pre-migration backup via the sqlite
online backup API, per-file transaction with a ledger table, and down
migrations require --down plus explicit confirmation. Never drops data unless
a down file was explicitly generated with allow_destructive=true AND you
passed --i-know-this-is-destructive.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

LEDGER = "_harmonika_migrations"
CONFIRM_FLAG = "--i-know-this-is-destructive"


def backup(db_path: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = db_path + ".pre-migration-" + stamp + ".bak"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    os.chmod(target, 0o600)
    return target


def main(argv):
    if len(argv) < 2:
        print("usage: apply_migration.py DB_PATH MIGRATIONS_DIR [--dialect sqlite] "
              "[--down NAME " + CONFIRM_FLAG + "]")
        return 2
    db_path, migrations_dir = argv[0], argv[1]
    dialect = "sqlite"
    down_name = None
    destructive = False
    i = 2
    while i < len(argv):
        if argv[i] == "--dialect" and i + 1 < len(argv):
            dialect = argv[i + 1]
            i += 2
        elif argv[i] == "--down" and i + 1 < len(argv):
            down_name = argv[i + 1]
            i += 2
        elif argv[i] == CONFIRM_FLAG:
            destructive = True
            i += 1
        else:
            print("unknown argument: " + argv[i])
            return 2
    if not os.path.exists(db_path):
        print("database_not_found: " + db_path)
        return 2
    if down_name:
        if not destructive:
            print("refused: down migrations are destructive; pass " + CONFIRM_FLAG)
            return 2
        path = os.path.join(migrations_dir, dialect, down_name + ".down.sql")
        if not os.path.exists(path):
            print("down_file_not_found: " + path)
            return 2
        conn = sqlite3.connect(db_path)
        conn.executescript(open(path).read())
        conn.commit()
        conn.close()
        print('{"applied_down": "%s"}' % down_name)
        return 0
    applied = []
    skipped = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    result = conn.execute("PRAGMA integrity_check").fetchone()
    if result[0] != "ok":
        print("integrity_check_failed: " + result[0])
        return 2
    conn.execute(
        "CREATE TABLE IF NOT EXISTS %s (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL "
        "DEFAULT CURRENT_TIMESTAMP)" % LEDGER)
    conn.commit()
    backup_target = backup(db_path)
    done = {r["name"] for r in conn.execute("SELECT name FROM " + LEDGER)}
    directory = os.path.join(migrations_dir, dialect)
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".up.sql"):
            continue
        if name in done:
            skipped.append(name)
            continue
        sql = open(os.path.join(directory, name)).read()
        conn.executescript(sql)  # script itself is wrapped in BEGIN/COMMIT
        conn.execute("INSERT INTO %s (name) VALUES (?)" % LEDGER, (name,))
        conn.commit()
        applied.append(name)
    conn.close()
    import json
    print(json.dumps({"applied": applied, "skipped": skipped,
                      "backup": backup_target}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

SEED_RUNNER = '''#!/usr/bin/env python3
"""Apply seed files (auto-generated, stdlib only).

Usage: python3 apply_seed.py DB_PATH SEED_DIR [--dialect sqlite]

Applies each seed/<dialect>.sql inside one transaction and reports inserted
row counts via total_changes. Idempotency comes from the generated SQL
(INSERT OR IGNORE / ON CONFLICT DO NOTHING) when a unique field exists.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys


def main(argv):
    if len(argv) < 2:
        print("usage: apply_seed.py DB_PATH SEED_DIR [--dialect sqlite]")
        return 2
    db_path, seed_dir = argv[0], argv[1]
    dialect = "sqlite"
    if len(argv) >= 4 and argv[2] == "--dialect":
        dialect = argv[3]
    path = os.path.join(seed_dir, dialect + ".sql")
    if not os.path.exists(db_path):
        print("database_not_found: " + db_path)
        return 2
    if not os.path.exists(path):
        print("seed_file_not_found: " + path)
        return 2
    conn = sqlite3.connect(db_path)
    result = conn.execute("PRAGMA integrity_check").fetchone()
    if result[0] != "ok":
        print("integrity_check_failed: " + result[0])
        return 2
    before = conn.total_changes
    conn.executescript(open(path).read())
    conn.commit()
    inserted = conn.total_changes - before
    conn.close()
    print(json.dumps({"seed": os.path.basename(path), "rows_affected": inserted}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def _run_migration(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    version = _opt_int(args, "version", 1, 1, 9999)
    fields = _fields(args)
    allow = _opt_bool(args, "allow_destructive", False)
    pairs = _migration_files(project, entity, table, "create_table", fields,
                             version, allow)
    pairs.append((safe.rel_path(project, "database", "run", "apply_migration.py"),
                  MIGRATION_RUNNER))
    return _result(
        {"project": project, "executed": False, "executed_by": "root sandbox",
         "status": "recipe_ready",
         "recipe": {
             "apply": "python3 database/run/apply_migration.py <db-path> database/migrations",
             "dialects": ["sqlite", "postgresql", "mysql"],
             "sql_files": [p for p, _ in pairs if p.endswith(".up.sql")],
             "safety": ["integrity_check first", "automatic pre-migration backup",
                        "ledger table prevents double-apply",
                        "down requires --i-know-this-is-destructive"]}},
        pairs,
        [EXEC_NOTE,
         "PostgreSQL/MySQL recipes must run under a privileged migration role; "
         "the sqlite runner is the only executable shipped here.",
         "No DROP by default: down files exist only when allow_destructive=true."])


def _run_seed(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    fields = _fields(args)
    count = _opt_int(args, "count", 5, 1, 50)
    custom = args.get("rows")
    if custom is not None and not isinstance(custom, list):
        raise safe.SkillInputError("rows: expected a list of rows")
    rows = _seed_rows(fields, count, custom)
    pairs = [(safe.rel_path(project, "database", "seed", d + ".sql"),
              _seed_sql(d, table, fields, rows))
             for d in ("sqlite", "postgresql", "mysql")]
    pairs.append((safe.rel_path(project, "database", "run", "apply_seed.py"),
                  SEED_RUNNER))
    return _result(
        {"project": project, "executed": False, "executed_by": "root sandbox",
         "status": "recipe_ready",
         "recipe": {
             "apply": "python3 database/run/apply_seed.py <db-path> database/seed",
             "dialects": ["sqlite", "postgresql", "mysql"],
             "sql_files": [p for p, _ in pairs if p.endswith(".sql")],
             "idempotent": any(f["unique"] for f in fields),
             "safety": ["integrity_check first", "single transaction per file"]}},
        pairs,
        [EXEC_NOTE,
         "Without a unique field, re-running the seed inserts duplicates; "
         "the recipe reports rows_affected so operators can verify."])


# ---------------------------------------------------------------------------
# 92 db.gen.indexes
# ---------------------------------------------------------------------------

def _gen_indexes(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    fields = _fields(args)
    names = {f["name"] for f in fields}
    query_fields = args.get("query_fields")
    if query_fields is None:
        # Prefer a required non-unique column: unique columns already have an
        # automatic unique index in most engines.
        preferred = next((f["name"] for f in fields
                          if f["required"] and not f["unique"]), None)
        query_fields = [preferred or fields[0]["name"]]
    if not isinstance(query_fields, list) or not 1 <= len(query_fields) <= 8:
        raise safe.SkillInputError("query_fields: expected a list of 1..8 field names")
    validated = []
    for name in query_fields:
        ident = safe.sql_ident(name, "query field")
        if ident not in names:
            raise safe.SkillInputError("query_fields: %s is not a known column" % ident)
        validated.append(ident)
    statements = {"sqlite": [], "postgresql": [], "mysql": []}
    for name in validated:
        for dialect in statements:
            statements[dialect].append(
                "CREATE INDEX IF NOT EXISTS idx_%s_%s ON %s (%s);"
                % (table, name, table, name))
    pairs = [(safe.rel_path(project, "database", "indexes", d + ".sql"),
              "-- indexes for %s (%s); generated by Harmonika web builder\n%s\n"
              % (table, d, "\n".join(stmts)))
             for d, stmts in statements.items()]
    return _result(
        {"project": project, "table": table, "indexed": validated,
         "verify": "EXPLAIN QUERY PLAN SELECT * FROM %s WHERE <field> = ?;"
                   % table},
        pairs,
        ["Index DDL is an artifact; apply via an authorized operator.",
         "Index needs depend on real query patterns; review EXPLAIN plans on "
         "production-sized data."])


# ---------------------------------------------------------------------------
# 93 db.gen.relations
# ---------------------------------------------------------------------------

def _gen_relations(args: Dict[str, Any]) -> dict:
    project = _project(args)
    parent = _entity(args, "category")
    child = _entity({**args, "entity": args.get("child_entity") or "product"})
    on_delete = args.get("on_delete", "restrict")
    if on_delete not in ("restrict", "cascade"):
        raise safe.SkillInputError("on_delete: must be restrict or cascade")
    many = _opt_bool(args, "many_to_many", False)
    sql_actions = {"restrict": "ON DELETE RESTRICT", "cascade": "ON DELETE CASCADE"}
    pairs = []
    child_table, parent_table = _table(child), _table(parent)
    fk = safe.sql_ident(child if many else parent, "fk column") + "_id"
    if many:
        tag = _entity({**args, "entity": args.get("second_entity") or "tag"})
        tag_table = _table(tag)
        join = safe.sql_ident(child_table + "_" + tag_table, "join table")
        fk_a = safe.sql_ident(child, "join fk a") + "_id"
        fk_b = safe.sql_ident(tag, "join fk b") + "_id"
        body = {
            "sqlite": (
                "CREATE TABLE IF NOT EXISTS %s (\n"
                "  %s INTEGER NOT NULL REFERENCES %s(id) %s,\n"
                "  %s INTEGER NOT NULL REFERENCES %s(id) %s,\n"
                "  PRIMARY KEY (%s, %s)\n"
                ");" % (join, fk_a, child_table, sql_actions[on_delete],
                        fk_b, tag_table, sql_actions[on_delete], fk_a, fk_b)),
            "postgresql": (
                "CREATE TABLE IF NOT EXISTS %s (\n"
                "  %s BIGINT NOT NULL REFERENCES %s(id) %s,\n"
                "  %s BIGINT NOT NULL REFERENCES %s(id) %s,\n"
                "  PRIMARY KEY (%s, %s)\n"
                ");" % (join, fk_a, child_table, sql_actions[on_delete],
                        fk_b, tag_table, sql_actions[on_delete], fk_a, fk_b)),
            "mysql": (
                "CREATE TABLE IF NOT EXISTS %s (\n"
                "  %s BIGINT UNSIGNED NOT NULL,\n"
                "  %s BIGINT UNSIGNED NOT NULL,\n"
                "  PRIMARY KEY (%s, %s),\n"
                "  FOREIGN KEY (%s) REFERENCES %s(id) %s,\n"
                "  FOREIGN KEY (%s) REFERENCES %s(id) %s\n"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
                % (join, fk_a, fk_b, fk_a, fk_b, fk_a, child_table,
                   sql_actions[on_delete], fk_b, tag_table, sql_actions[on_delete])),
        }
        data = {"project": project, "relation": "N-N", "join_table": join,
                "entities": [child_table, tag_table]}
    else:
        body = {
            "sqlite": "-- apply on the child table definition or via ALTER (3.35+)\n"
                      "ALTER TABLE %s ADD COLUMN %s INTEGER REFERENCES %s(id) %s;"
                      % (child_table, fk, parent_table, sql_actions[on_delete]),
            "postgresql": "ALTER TABLE %s ADD CONSTRAINT fk_%s_%s "
                          "FOREIGN KEY (%s) REFERENCES %s(id) %s;"
                          % (child_table, child_table, parent_table, fk,
                             parent_table, sql_actions[on_delete]),
            "mysql": "ALTER TABLE %s ADD COLUMN %s BIGINT UNSIGNED NULL,\n"
                     "ADD CONSTRAINT fk_%s_%s FOREIGN KEY (%s) REFERENCES %s(id) %s;"
                     % (child_table, fk, child_table, parent_table, fk,
                        parent_table, sql_actions[on_delete]),
        }
        data = {"project": project, "relation": "1-N", "parent": parent_table,
                "child": child_table, "fk_column": fk, "on_delete": on_delete}
    for dialect in ("sqlite", "postgresql", "mysql"):
        pairs.append((safe.rel_path(project, "database", "relations", dialect + ".sql"),
                      "-- %s relation; generated by Harmonika web builder\n%s\n"
                      % (dialect, body[dialect])))
    return _result(data, pairs,
                   ["FK DDL is an artifact; apply via an authorized operator.",
                    "SQLite enforces foreign keys only with PRAGMA foreign_keys=ON "
                    "per connection.",
                    "Many-to-many generated only with many_to_many=true."])


# ---------------------------------------------------------------------------
# 94 db.gen.triggers
# ---------------------------------------------------------------------------

def _gen_triggers(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    fields = _fields(args)
    audit = safe.sql_ident(table + "_audit", "audit table")
    cols = ["id"] + [f["name"] for f in fields]
    snap = " || '|' || ".join("IFNULL(OLD.%s, '')" % c for c in cols)

    sqlite_sql = (
        "CREATE TABLE IF NOT EXISTS {audit} (\n"
        "  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "  op TEXT NOT NULL, row_id INTEGER, snapshot TEXT NOT NULL,\n"
        "  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n"
        ");\n"
        "CREATE TRIGGER IF NOT EXISTS trg_{table}_ins AFTER INSERT ON {table}\n"
        "BEGIN INSERT INTO {audit} (op, row_id, snapshot) "
        "VALUES ('INSERT', NEW.id, NEW.id || '|' || NEW.{first}); END;\n"
        "CREATE TRIGGER IF NOT EXISTS trg_{table}_upd AFTER UPDATE ON {table}\n"
        "BEGIN INSERT INTO {audit} (op, row_id, snapshot) "
        "VALUES ('UPDATE', OLD.id, {snap}); END;\n"
        "CREATE TRIGGER IF NOT EXISTS trg_{table}_del AFTER DELETE ON {table}\n"
        "BEGIN INSERT INTO {audit} (op, row_id, snapshot) "
        "VALUES ('DELETE', OLD.id, {snap}); END;\n"
    ).format(audit=audit, table=table, snap=snap, first=cols[1])

    pg_sql = (
        "CREATE TABLE IF NOT EXISTS {audit} (\n"
        "  audit_id BIGSERIAL PRIMARY KEY,\n"
        "  op TEXT NOT NULL, row_id BIGINT, snapshot JSONB NOT NULL,\n"
        "  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()\n"
        ");\n"
        "CREATE OR REPLACE FUNCTION {audit}_fn() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "  INSERT INTO {audit} (op, row_id, snapshot)\n"
        "  VALUES (TG_OP, COALESCE(NEW.id, OLD.id), to_jsonb(COALESCE(NEW, OLD)));\n"
        "  RETURN COALESCE(NEW, OLD);\n"
        "END; $$ LANGUAGE plpgsql;\n"
        "DROP TRIGGER IF EXISTS trg_{table}_audit ON {table};\n"
        "CREATE TRIGGER trg_{table}_audit AFTER INSERT OR UPDATE OR DELETE ON {table}\n"
        "FOR EACH ROW EXECUTE FUNCTION {audit}_fn();\n"
    ).format(audit=audit, table=table)

    snap_args = ", ".join("OLD.%s" % c for c in cols)
    mysql_sql = (
        "DELIMITER $$\n"
        "CREATE TRIGGER trg_{table}_ins AFTER INSERT ON {table} FOR EACH ROW\n"
        "BEGIN INSERT INTO {audit} (op, row_id, snapshot) "
        "VALUES ('INSERT', NEW.id, CONCAT_WS('|', NEW.id, NEW.{first})); END$$\n"
        "CREATE TRIGGER trg_{table}_upd AFTER UPDATE ON {table} FOR EACH ROW\n"
        "BEGIN INSERT INTO {audit} (op, row_id, snapshot) "
        "VALUES ('UPDATE', OLD.id, CONCAT_WS('|', {snap_args})); END$$\n"
        "CREATE TRIGGER trg_{table}_del AFTER DELETE ON {table} FOR EACH ROW\n"
        "BEGIN INSERT INTO {audit} (op, row_id, snapshot) "
        "VALUES ('DELETE', OLD.id, CONCAT_WS('|', {snap_args})); END$$\n"
        "DELIMITER ;\n"
    ).format(audit=audit, table=table, first=cols[1], snap_args=snap_args)
    pairs = [(safe.rel_path(project, "database", "triggers", d + ".sql"), src)
             for d, src in (("sqlite", sqlite_sql), ("postgresql", pg_sql),
                            ("mysql", mysql_sql))]
    return _result(
        {"project": project, "table": table, "audit_table": audit,
         "triggers": ["INSERT", "UPDATE", "DELETE"]},
        pairs,
        ["Trigger DDL is an artifact; apply via an authorized operator.",
         "Audit snapshots keep the row values; size the audit table and add "
         "retention pruning before production."])


# ---------------------------------------------------------------------------
# 95 db.gen.stored_proc
# ---------------------------------------------------------------------------

def _gen_stored_proc(args: Dict[str, Any]) -> dict:
    project = _project(args)
    entity = _entity(args)
    table = _table(entity)
    fields = _fields(args)
    numeric = [f for f in fields if f["type"] in ("int", "float")]
    if not numeric:
        raise safe.SkillInputError(
            "stored_proc reserve_quantity needs an int/float field (e.g. stock)")
    preferred = next((f["name"] for f in numeric
                      if f["name"] in ("stock", "quantity", "qty", "balance")), None)
    qty = safe.sql_ident(preferred or numeric[0]["name"], "quantity column")
    fn = safe.sql_ident("reserve_" + table, "routine name")

    pg_sql = (
        "-- PostgreSQL: atomic guarded decrement; raises on insufficient stock.\n"
        "CREATE OR REPLACE FUNCTION {fn}(p_id BIGINT, p_qty BIGINT)\n"
        "RETURNS BIGINT AS $$\n"
        "DECLARE\n"
        "  remaining BIGINT;\n"
        "BEGIN\n"
        "  UPDATE {table} SET {qty} = {qty} - p_qty\n"
        "   WHERE id = p_id AND {qty} >= p_qty\n"
        "   RETURNING {qty} INTO remaining;\n"
        "  IF remaining IS NULL THEN\n"
        "    RAISE EXCEPTION 'insufficient_{qty}' USING ERRCODE = '23514';\n"
        "  END IF;\n"
        "  RETURN remaining;\n"
        "END; $$ LANGUAGE plpgsql;\n"
    ).format(fn=fn, table=table, qty=qty)

    mysql_sql = (
        "DELIMITER $$\n"
        "CREATE PROCEDURE {fn}(IN p_id BIGINT UNSIGNED, IN p_qty BIGINT)\n"
        "BEGIN\n"
        "  UPDATE {table} SET {qty} = {qty} - p_qty\n"
        "   WHERE id = p_id AND {qty} >= p_qty;\n"
        "  IF ROW_COUNT() = 0 THEN\n"
        "    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'insufficient_{qty}';\n"
        "  END IF;\n"
        "END$$\n"
        "DELIMITER ;\n"
    ).format(fn=fn, table=table, qty=qty)

    sqlite_sql = (
        "-- SQLite has no stored procedures; the safe equivalent is a single\n"
        "-- guarded UPDATE executed inside a transaction (race-free via write lock).\n"
        "BEGIN IMMEDIATE;\n"
        "UPDATE {table} SET {qty} = {qty} - :qty\n"
        " WHERE id = :id AND {qty} >= :qty;\n"
        "-- check cursor.rowcount == 1 else ROLLBACK (insufficient {qty});\n"
        "COMMIT;\n"
    ).format(table=table, qty=qty)
    pairs = [(safe.rel_path(project, "database", "procedures", d + ".sql"), src)
             for d, src in (("postgresql", pg_sql), ("mysql", mysql_sql),
                            ("sqlite", sqlite_sql))]
    return _result(
        {"project": project, "table": table, "routine": fn, "guards": qty,
         "dialects": {"postgresql": "plpgsql function RETURNS BIGINT",
                      "mysql": "SIGNAL SQLSTATE 45000 on ROW_COUNT()=0",
                      "sqlite": "guarded UPDATE in BEGIN IMMEDIATE transaction"}},
        pairs,
        ["Routine DDL is an artifact; deploy via an authorized operator with "
         "CREATE ROUTINE privileges.",
         "SQLite genuinely has no stored procedures; the shipped equivalent is "
         "the guarded single-statement transaction."])


# ---------------------------------------------------------------------------
# 96 db.backup.automate
# ---------------------------------------------------------------------------

BACKUP_RUNNER = '''#!/usr/bin/env python3
"""SQLite backup with retention (auto-generated, stdlib only).

Usage: python3 backup.py DB_PATH --out BACKUP_DIR [--keep N] [--gzip]

Uses the sqlite online backup API (safe while the app writes), verifies the
copy with integrity_check, and prunes only files matching
<db>.backup-*.sqlite (the live database is never touched by pruning).
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import sys
import time


def _prune(directory: str, prefix: str, keep: int) -> int:
    candidates = sorted(
        (f for f in os.listdir(directory) if f.startswith(prefix)),
        key=lambda f: os.path.getmtime(os.path.join(directory, f)))
    removed = 0
    for name in candidates[:-keep] if keep > 0 else candidates:
        os.remove(os.path.join(directory, name))
        removed += 1
    return removed


def main(argv):
    if len(argv) < 1:
        print("usage: backup.py DB_PATH --out BACKUP_DIR [--keep N] [--gzip]")
        return 2
    db_path = argv[0]
    out_dir = os.path.join(os.path.dirname(db_path) or ".", "backups")
    keep = 7
    use_gzip = False
    i = 1
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--keep" and i + 1 < len(argv):
            keep = int(argv[i + 1])
            i += 2
        elif argv[i] == "--gzip":
            use_gzip = True
            i += 1
        else:
            print("unknown argument: " + argv[i])
            return 2
    if not os.path.exists(db_path):
        print("database_not_found: " + db_path)
        return 2
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(db_path)
    target = os.path.join(out_dir, "%s.backup-%s.sqlite" % (base, stamp))
    src = sqlite3.connect(db_path)
    check = src.execute("PRAGMA integrity_check").fetchone()
    if check[0] != "ok":
        print("source_integrity_failed: " + check[0])
        return 2
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    verify = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    if verify != "ok":
        os.remove(target)
        print("backup_verify_failed")
        return 2
    os.chmod(target, 0o600)
    if use_gzip:
        gz = target + ".gz"
        with open(target, "rb") as fin, gzip.open(gz, "wb") as fout:
            fout.write(fin.read())
        os.remove(target)
        target = gz
    removed = _prune(out_dir, base + ".backup-", max(1, keep))
    print(json.dumps({"backup": target, "verified": True, "pruned": removed,
                      "keep": keep}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def _gen_backup(args: Dict[str, Any]) -> dict:
    project = _project(args)
    keep = _opt_int(args, "keep", 7, 1, 365)
    return _result(
        {"project": project,
         "recipe": {
             "sqlite": "python3 database/run/backup.py <db-path> --out backups --keep %d --gzip" % keep,
             "postgresql": "pg_dump --no-owner --format=custom --file=backup.dump $DATABASE_URL",
             "mysql": "mysqldump --single-transaction --routines --triggers "
                      "--result-file=backup.sql $DATABASE_URL",
             "scheduling": "operator cron / root orchestration only; this skill "
                           "installs no scheduler"},
         "retention": {"keep": keep, "scope": "backup files only, never live data"}},
        [(safe.rel_path(project, "database", "run", "backup.py"), BACKUP_RUNNER)],
        ["Backup scheduling is left to the operator (cron/systemd); no host "
         "scheduler is installed by this skill.",
         "pg_dump/mysqldump recipes require those binaries and credentials "
         "configured by the operator; the executable runner here is SQLite-only.",
         "Retention pruning deletes only files matching <db>.backup-*.sqlite."])


# ---------------------------------------------------------------------------
# SKILLS registry (IDs 87-96)
# ---------------------------------------------------------------------------

def _entry(number: int, summary: str, schema: dict, handler) -> dict:
    return {"number": number, "summary": summary, "schema": schema, "handler": handler}


def _schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": [], "additionalProperties": False}


PROJECT_PROP = {"type": "string", "pattern": "^[a-z][a-z0-9]*(-[a-z0-9]+)*$",
                "minLength": 2, "maxLength": 50}
ENTITY_PROP = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"}
FIELDS_PROP = {
    "type": "array", "minItems": 1, "maxItems": 24,
    "items": {"type": "object",
              "properties": {"name": {"type": "string",
                                      "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
                             "type": {"enum": list(FIELD_TYPES)},
                             "required": {"type": "boolean"},
                             "unique": {"type": "boolean"},
                             "max": {"type": "integer", "minimum": 1, "maximum": 4000}},
              "additionalProperties": False}}
ROWS_PROP = {"type": "array", "maxItems": 50,
             "items": {"type": "array", "maxItems": 24}}

_CORE_PROPS = {"project": PROJECT_PROP, "entity": ENTITY_PROP, "fields": FIELDS_PROP}

SKILLS: Dict[str, dict] = {
    "db.gen.schema": _entry(87, "Generate real-dialect CREATE TABLE schema (SQLite/PostgreSQL/MySQL)",
                            _schema(_CORE_PROPS), _gen_schema),
    "db.gen.migration": _entry(88, "Generate transactional migration up/down (no DROP by default)",
                               _schema(dict(_CORE_PROPS,
                                            change={"enum": ["create_table", "add_columns"]},
                                            version={"type": "integer", "minimum": 1, "maximum": 9999},
                                            allow_destructive={"type": "boolean"})),
                               _gen_migration),
    "db.gen.seed": _entry(89, "Generate idempotent seed data with escaped literals",
                          _schema(dict(_CORE_PROPS,
                                       count={"type": "integer", "minimum": 1, "maximum": 50},
                                       rows=ROWS_PROP)),
                          _gen_seed),
    "db.run.migration": _entry(90, "Return migration recipe + runner; execution happens in root sandbox",
                               _schema(dict(_CORE_PROPS,
                                            version={"type": "integer", "minimum": 1, "maximum": 9999},
                                            allow_destructive={"type": "boolean"})),
                               _run_migration),
    "db.run.seed": _entry(91, "Return seed recipe + runner; execution happens in root sandbox",
                          _schema(dict(_CORE_PROPS,
                                       count={"type": "integer", "minimum": 1, "maximum": 50},
                                       rows=ROWS_PROP)),
                          _run_seed),
    "db.gen.indexes": _entry(92, "Generate CREATE INDEX per dialect for query fields",
                             _schema(dict(_CORE_PROPS,
                                          query_fields={"type": "array", "minItems": 1,
                                                        "maxItems": 8,
                                                        "items": {"type": "string"}})),
                             _gen_indexes),
    "db.gen.relations": _entry(93, "Generate FK relations (1-N) or join table (N-N) per dialect",
                               _schema(dict(_CORE_PROPS,
                                            child_entity=ENTITY_PROP,
                                            second_entity=ENTITY_PROP,
                                            on_delete={"enum": ["restrict", "cascade"]},
                                            many_to_many={"type": "boolean"})),
                               _gen_relations),
    "db.gen.triggers": _entry(94, "Generate audit-log triggers (INSERT/UPDATE/DELETE) per dialect",
                              _schema(_CORE_PROPS), _gen_triggers),
    "db.gen.stored_proc": _entry(95, "Generate guarded reserve_quantity routine (plpgsql/MySQL; SQLite equivalent)",
                                 _schema(_CORE_PROPS), _gen_stored_proc),
    "db.backup.automate": _entry(96, "Generate SQLite backup runner with retention + dump recipes",
                                 _schema({"project": PROJECT_PROP,
                                          "keep": {"type": "integer", "minimum": 1, "maximum": 365}}),
                                 _gen_backup),
}
