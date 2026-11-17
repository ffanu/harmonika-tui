"""Harmonika Knowledge Base — tenant-scoped SQLite semantic retrieval.

The loopback worker accepts X-Harmonika-KB-Owner only from its trusted gateway.
The gateway must overwrite that header with token:<verified token database ID>.
Unowned legacy records remain stored and are hidden from all token scopes.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import math
import os
import re
import sqlite3
import struct
import threading
import time
import urllib.request

DB_PATH = os.environ.get("HARMONIKA_KB_DB_PATH", "/var/lib/harmonika-kb/knowledge.db")
EMBED_URL = os.environ.get("HARMONIKA_KB_EMBED_URL", "http://127.0.0.1:17897/embed")
RERANK_URL = os.environ.get("HARMONIKA_KB_RERANK_URL", "http://127.0.0.1:17899/rerank")
DIM = 384
MAX_SEARCH_ROWS = 4096
MAX_BODY_BYTES = 2 * 1024 * 1024
OWNER_HEADER = "X-Harmonika-KB-Owner"


def owner_scope(value):
    if not isinstance(value, str) or not re.fullmatch(r"token:[1-9][0-9]{0,19}", value):
        raise ValueError("owner_required")
    return value


def bounded_int(value, default, maximum):
    if value is None:
        return default
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        result = int(value)
    else:
        raise ValueError("invalid_limit")
    if not 1 <= result <= maximum:
        raise ValueError("invalid_limit")
    return result


def text_field(value, maximum, code, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(code)
    return value


def embed_one(text):
    body = json.dumps({"texts": [text[:8000]]}).encode()
    req = urllib.request.Request(EMBED_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.loads(response.read(2 * 1024 * 1024))
    return data["vectors"][0]


def rerank_via_service(query, texts, top_k):
    body = json.dumps({"query": query, "documents": texts, "top_k": top_k}).encode()
    request = urllib.request.Request(RERANK_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read(2 * 1024 * 1024))["results"]


def vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != DIM:
        raise ValueError("embedding_invalid")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError("embedding_invalid")
    norm = math.sqrt(sum(x * x for x in value)) or 1.0
    if not math.isfinite(norm):
        raise ValueError("embedding_invalid")
    return [x / norm for x in value]


def pack(value):
    return struct.pack(f"{len(value)}f", *value)


def unpack(blob):
    if not isinstance(blob, bytes) or len(blob) != DIM * 4:
        raise ValueError("knowledge_store_invalid")
    value = struct.unpack(f"{DIM}f", blob)
    if any(not math.isfinite(x) for x in value):
        raise ValueError("knowledge_store_invalid")
    return value


class KnowledgeBase:
    def __init__(self, db_path=DB_PATH, embedder=None, max_search_rows=MAX_SEARCH_ROWS):
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        # Existing databases also become tenant-private after migration.
        os.chmod(db_path, 0o600)
        self.lock = threading.Lock()
        self.embed = embedder or embed_one
        self.max_search_rows = bounded_int(max_search_rows, MAX_SEARCH_ROWS, MAX_SEARCH_ROWS)
        # Additive migration; no existing content is assigned to an arbitrary tenant.
        self.db.execute("""CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT DEFAULT '', kind TEXT DEFAULT 'note', tags TEXT DEFAULT '[]',
            text TEXT NOT NULL, vector BLOB NOT NULL, created_at INTEGER NOT NULL,
            owner TEXT NOT NULL DEFAULT '')""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(chunks)")}
        if "owner" not in columns:
            self.db.execute("ALTER TABLE chunks ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_kind ON chunks(kind)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_owner_kind_id ON chunks(owner, kind, id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_owner_id ON chunks(owner, id)")
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def count(self, owner=None):
        with self.lock:
            if owner is None:
                return self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]
            return self.db.execute("SELECT count(*) FROM chunks WHERE owner=?", (owner_scope(owner),)).fetchone()[0]

    def store(self, owner, text, source="", kind="note", tags=None):
        owner = owner_scope(owner)
        text = text_field(text, 60000, "invalid_text").strip()
        source = text_field(source, 300, "invalid_source", empty=True)
        kind = text_field(kind, 40, "invalid_kind")
        tags = [] if tags is None else tags
        if not isinstance(tags, list) or len(tags) > 32 or any(
            not isinstance(tag, str) or not tag or len(tag) > 80 for tag in tags
        ):
            raise ValueError("invalid_tags")
        embedding = vector(self.embed(text))
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO chunks (owner,source,kind,tags,text,vector,created_at) VALUES (?,?,?,?,?,?,?)",
                (owner, source, kind, json.dumps(tags), text, pack(embedding), int(time.time())))
            self.db.commit()
            return cur.lastrowid

    def search(self, owner, query, limit=5, kind=None, tag=None, rerank=False, *, metadata=None):
        owner = owner_scope(owner)
        if type(rerank) is not bool:
            raise ValueError("invalid_fields")
        if metadata is not None:
            metadata.update(rerank_requested=rerank, rerank_used=False)
        query = text_field(query, 8000, "invalid_query").strip()
        limit = bounded_int(limit, 5, 20)
        if kind is not None:
            kind = text_field(kind, 40, "invalid_kind")
        if tag is not None:
            tag = text_field(tag, 80, "invalid_tag")
        qvec = vector(self.embed(query))
        sql = "SELECT id,source,kind,tags,substr(text,1,2000),vector,created_at FROM chunks WHERE owner=?"
        params = [owner]
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(self.max_search_rows + 1)
        with self.lock:
            rows = self.db.execute(sql, params).fetchall()
        if len(rows) > self.max_search_rows:
            # A partial scan cannot truthfully claim to find the most relevant notes.
            raise ValueError("search_scope_limit")
        scored = []
        for rid, source, stored_kind, raw_tags, text, blob, created in rows:
            tags = json.loads(raw_tags or "[]")
            if tag is not None and tag not in tags:
                continue
            score = sum(a * b for a, b in zip(qvec, unpack(blob)))
            scored.append((score, rid, source, stored_kind, tags, text, created))
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        results = [{"id": rid, "score": round(score, 4), "source": source,
                    "kind": stored_kind, "tags": tags, "text": text, "created_at": created}
                   for score, rid, source, stored_kind, tags, text, created in scored]
        if rerank and len(results) > 1:
            # The reranker receives only this owner's already-filtered candidates.
            # Its response can select indices, never supply replacement note text.
            pool = results[:20]
            try:
                ranked = rerank_via_service(query, [row["text"] for row in pool], len(pool))
                valid = isinstance(ranked, list) and len(ranked) == len(pool) and all(
                    isinstance(row, dict) and type(row.get("index")) is int
                    and 0 <= row["index"] < len(pool)
                    and type(row.get("score")) in (int, float)
                    and math.isfinite(row["score"]) for row in ranked)
                if valid and {row["index"] for row in ranked} == set(range(len(pool))):
                    ordered = sorted(ranked, key=lambda row: row["score"], reverse=True)
                    reranked = [dict(pool[row["index"]], rerank_score=row["score"])
                                for row in ordered[:limit]]
                    if metadata is not None:
                        metadata["rerank_used"] = True
                    return reranked
            except Exception:
                # Preserve semantic fallback without exposing service error details.
                pass
        return results[:limit]

    def list(self, owner, limit=20):
        owner = owner_scope(owner)
        limit = bounded_int(limit, 20, 100)
        with self.lock:
            rows = self.db.execute(
                "SELECT id,source,kind,tags,substr(text,1,200),created_at FROM chunks WHERE owner=? ORDER BY id DESC LIMIT ?",
                (owner, limit)).fetchall()
        return [{"id": row[0], "source": row[1], "kind": row[2],
                 "tags": json.loads(row[3] or "[]"), "preview": row[4], "created_at": row[5]}
                for row in rows]

    def delete(self, owner, rid):
        owner = owner_scope(owner)
        if type(rid) is not int or rid < 1:
            raise ValueError("invalid_id")
        with self.lock:
            cur = self.db.execute("DELETE FROM chunks WHERE owner=? AND id=?", (owner, rid))
            self.db.commit()
            return cur.rowcount > 0


ERROR_CODES = {"owner_required", "invalid_limit", "invalid_text", "invalid_source", "invalid_kind",
               "invalid_tags", "invalid_query", "invalid_tag", "invalid_id", "embedding_invalid",
               "knowledge_store_invalid", "search_scope_limit", "invalid_fields"}


class Handler(BaseHTTPRequestHandler):
    def _owner(self):
        values = self.headers.get_all(OWNER_HEADER) or []
        return owner_scope(values[0] if len(values) == 1 else None)

    def _failure(self, error):
        code = str(error) if isinstance(error, ValueError) and str(error) in ERROR_CODES else "knowledge_unavailable"
        self._json({"error": code}, 403 if code == "owner_required" else 503 if code == "knowledge_unavailable" else 400)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/health":
                self._json({"ok": True, "service": "harmonika-knowledge", "entries": self.server.knowledge.count()})
            elif url.path == "/list":
                owner = self._owner()
                query = parse_qs(url.query)
                if set(query) - {"limit"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("invalid_fields")
                limit = bounded_int(query.get("limit", [None])[0], 20, 100)
                self._json({"items": self.server.knowledge.list(owner, limit)})
            else:
                self._json({"error": "not_found"}, 404)
        except Exception as error:
            self._failure(error)

    def do_POST(self):
        try:
            owner = self._owner()
            raw_length = self.headers.get("Content-Length") or "0"
            if not re.fullmatch(r"[0-9]{1,10}", raw_length):
                self._json({"error": "invalid_body_length"}, 400)
                return
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                self._json({"error": "body_too_large"}, 413)
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeError):
                self._json({"error": "invalid_json"}, 400)
                return
            if not isinstance(data, dict):
                raise ValueError("invalid_fields")
            route = urlparse(self.path).path
            allowed = {"/store": {"text", "source", "kind", "tags"},
                       "/search": {"query", "limit", "kind", "tag", "rerank"}, "/delete": {"id"}}
            if route not in allowed:
                self._json({"error": "not_found"}, 404)
                return
            if set(data) - allowed[route]:
                raise ValueError("invalid_fields")
            if route == "/store":
                rid = self.server.knowledge.store(owner, data.get("text"), data.get("source", ""),
                                                  data.get("kind", "note"), data.get("tags"))
                self._json({"id": rid, "stored": True})
            elif route == "/search":
                if "rerank" in data and type(data["rerank"]) is not bool:
                    raise ValueError("invalid_fields")
                metadata = {}
                results = self.server.knowledge.search(owner, data.get("query"), data.get("limit", 5),
                                                       data.get("kind"), data.get("tag"),
                                                       data.get("rerank", False), metadata=metadata)
                reply = {"results": results}
                if "rerank" in data:
                    reply.update(metadata)
                self._json(reply)
            else:
                self._json({"deleted": self.server.knowledge.delete(owner, data.get("id"))})
        except Exception as error:
            self._failure(error)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


def make_server(db_path=DB_PATH, host="127.0.0.1", port=17898, embedder=None):
    server = HTTPServer((host, port), Handler)
    server.knowledge = KnowledgeBase(db_path, embedder=embedder)
    return server


def main():
    server = make_server()
    print("Knowledge Base on 127.0.0.1:17898", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.knowledge.close()


if __name__ == "__main__":
    main()
