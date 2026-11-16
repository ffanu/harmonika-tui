"""Harmonika Knowledge Base — SQLite + semantic vector search.
Port 17898. Depends on harmonika-semantic (17897) for embeddings.

Endpoints:
  GET  /health
  POST /store   {"text","source?","kind?","tags?"} -> {"id"}
  POST /search  {"query","limit?","kind?","tag?"}  -> {"results":[{id,score,source,kind,text,created_at}]}
  GET  /list?limit=N                               -> {"items":[...]}
  POST /delete  {"id"}                             -> {"deleted": true}
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json, sqlite3, struct, time, math, urllib.request, os

DB_DIR = "/var/lib/harmonika-kb"
DB_PATH = os.path.join(DB_DIR, "knowledge.db")
EMBED_URL = "http://127.0.0.1:17897/embed"
DIM = 384

os.makedirs(DB_DIR, exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT DEFAULT '',
    kind TEXT DEFAULT 'note',
    tags TEXT DEFAULT '[]',
    text TEXT NOT NULL,
    vector BLOB NOT NULL,
    created_at INTEGER NOT NULL
)""")
db.execute("CREATE INDEX IF NOT EXISTS idx_kind ON chunks(kind)")
db.commit()

import threading
_lock = threading.Lock()

def embed_one(text):
    body = json.dumps({"texts": [text[:8000]]}).encode()
    req = urllib.request.Request(EMBED_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    vec = data["vectors"][0]
    # L2 normalize at write time -> cosine == dot product
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]

def pack(vec):
    return struct.pack(f"{len(vec)}f", *vec)

def unpack(blob):
    n = len(blob) // 4
    return struct.unpack(f"{n}f", blob)

def store(text, source="", kind="note", tags=None):
    vec = embed_one(text)
    now = int(time.time())
    with _lock:
        cur = db.execute(
            "INSERT INTO chunks (source, kind, tags, text, vector, created_at) VALUES (?,?,?,?,?,?)",
            (source[:300], kind[:40], json.dumps(tags or []), text[:60000], pack(vec), now))
        db.commit()
        return cur.lastrowid

def search(query, limit=5, kind=None, tag=None):
    qvec = embed_one(query)
    with _lock:
        rows = db.execute(
            "SELECT id, source, kind, tags, text, vector, created_at FROM chunks").fetchall()
    scored = []
    for rid, source, k, tags_json, text, blob, created in rows:
        if kind and k != kind:
            continue
        if tag and tag not in json.loads(tags_json or "[]"):
            continue
        v = unpack(blob)
        score = sum(a * b for a, b in zip(qvec, v))
        scored.append((score, rid, source, k, tags_json, text, created))
    scored.sort(reverse=True)
    return [
        {"id": rid, "score": round(score, 4), "source": source, "kind": k,
         "tags": json.loads(tags_json or "[]"), "text": text[:2000], "created_at": created}
        for score, rid, source, k, tags_json, text, created in scored[:limit]
    ]

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            count = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
            self._json({"ok": True, "service": "harmonika-knowledge", "entries": count})
            return
        if u.path == "/list":
            q = parse_qs(u.query)
            limit = min(int(q.get("limit", ["20"])[0] or 20), 100)
            with _lock:
                rows = db.execute(
                    "SELECT id, source, kind, tags, substr(text,1,200), created_at FROM chunks ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            self._json({"items": [
                {"id": r[0], "source": r[1], "kind": r[2], "tags": json.loads(r[3] or "[]"),
                 "preview": r[4], "created_at": r[5]} for r in rows]})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2 * 1024 * 1024:
            self._json({"error": "body_too_large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return
        try:
            if self.path == "/store":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    self._json({"error": "text required"}, 400)
                    return
                rid = store(text.strip(), str(data.get("source") or ""),
                            str(data.get("kind") or "note"), data.get("tags") or [])
                self._json({"id": rid, "stored": True})
                return
            if self.path == "/search":
                query = data.get("query")
                if not isinstance(query, str) or not query.strip():
                    self._json({"error": "query required"}, 400)
                    return
                limit = min(int(data.get("limit") or 5), 20)
                results = search(query.strip(), limit, data.get("kind"), data.get("tag"))
                self._json({"results": results})
                return
            if self.path == "/delete":
                rid = data.get("id")
                if not isinstance(rid, int):
                    self._json({"error": "id required"}, 400)
                    return
                with _lock:
                    cur = db.execute("DELETE FROM chunks WHERE id=?", (rid,))
                    db.commit()
                self._json({"deleted": cur.rowcount > 0})
                return
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("Knowledge Base on 127.0.0.1:17898", flush=True)
HTTPServer(("127.0.0.1", 17898), Handler).serve_forever()
