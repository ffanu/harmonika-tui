"""Harmonika Rerank service — CrossEncoder (BAAI/bge-reranker-base, multilingual).
Port 17899.
  GET  /health
  POST /rerank {"query": "...", "documents": ["...", ...], "top_k"?}
       -> {"results": [{"index": 0, "score": 6.32}, ...]} (sorted desc)
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, threading

MODEL = "BAAI/bge-reranker-base"
_lock = threading.Lock()
_model = None

def get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        _model = CrossEncoder(MODEL, max_length=512)
    return _model

def rerank(query, docs, top_k):
    with _lock:
        scores = get_model().predict([(query[:2000], d[:2000]) for d in docs])
    pairs = sorted(enumerate(float(s) for s in scores), key=lambda x: -x[1])
    return [{"index": i, "score": round(s, 4)} for i, s in pairs[:top_k]]

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "harmonika-rerank", "model": MODEL})
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
        if self.path != "/rerank":
            self._json({"error": "not found"}, 404)
            return
        q = data.get("query")
        docs = data.get("documents")
        if not isinstance(q, str) or not q.strip() or not isinstance(docs, list) or not docs:
            self._json({"error": "query and documents required"}, 400)
            return
        if len(docs) > 64:
            self._json({"error": "max 64 documents"}, 400)
            return
        try:
            results = rerank(q.strip(), [str(d)[:8000] for d in docs],
                             min(int(data.get("top_k") or len(docs)), len(docs)))
            self._json({"results": results})
        except Exception as e:
            self._json({"error": f"rerank_failed: {e}"}, 500)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("Rerank service on 127.0.0.1:17899", flush=True)
HTTPServer(("127.0.0.1", 17899), Handler).serve_forever()
