"""Harmonika Semantic Embedding service — fastembed + multilingual MiniLM.
Port 17897. Endpoints:
  GET  /health
  POST /embed    {"texts": ["...", ...]}        -> {"vectors": [[...]], "dim": 384}
  POST /similarity {"texts": [...], "query": ""} -> {"scores": [0.87, ...]}
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, threading

from fastembed import TextEmbedding

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384
_lock = threading.Lock()
_model = None

def get_model():
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=MODEL)
    return _model

def embed(texts):
    with _lock:
        return [list(map(float, v)) for v in get_model().embed(texts)]

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "harmonika-embedding", "model": MODEL, "dim": DIM})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4 * 1024 * 1024:
            self._json({"error": "body_too_large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return

        if self.path == "/embed":
            texts = data.get("texts")
            if not isinstance(texts, list) or not texts or len(texts) > 64:
                self._json({"error": "texts must be a list of 1-64 strings"}, 400)
                return
            texts = [str(t)[:8000] for t in texts]
            try:
                vectors = embed(texts)
            except Exception as e:
                self._json({"error": f"embed_failed: {e}"}, 500)
                return
            self._json({"vectors": vectors, "dim": DIM, "model": MODEL})
            return

        if self.path == "/similarity":
            texts = data.get("texts") or []
            query = data.get("query") or ""
            if not texts or not query:
                self._json({"error": "texts and query required"}, 400)
                return
            try:
                vecs = embed([query] + [str(t)[:8000] for t in texts[:64]])
                q, rest = vecs[0], vecs[1:]
                scores = [sum(a * b for a, b in zip(q, v)) for v in rest]
                self._json({"scores": scores})
            except Exception as e:
                self._json({"error": f"similarity_failed: {e}"}, 500)
            return

        self._json({"error": "not found"}, 404)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("Embedding service on 127.0.0.1:17897", flush=True)
HTTPServer(("127.0.0.1", 17897), Handler).serve_forever()
