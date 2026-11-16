"""Harmonika Content Safety — heuristic rule-based text moderation (v1).
Port 17903. NOT a trained classifier; pattern heuristics with severity.
  GET  /health
  POST /check {"text"} -> {"safe", "risk", "flags": [...], "blocked_terms": n}
"""
import json, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PATTERNS = [
    (re.compile(r"\b(kill yourself|kys|bunuh diri)\b", re.I), "self_harm", 0.9),
    (re.compile(r"\b(how to (make|build) (a )?(bomb|explosive|pipe bomb))\b", re.I), "explosives", 0.95),
    (re.compile(r"\b(child|underage|minor)\s+(porn|sexual|nude)\b|\bcsam\b", re.I), "csam", 1.0),
    (re.compile(r"\b(credit card|kartu kredit)\b.*\b(dump|skimmer|stolen|curi)\b", re.I), "fraud", 0.8),
    (re.compile(r"\b(ransomware|keylogger)\b.*\b(write|create|deploy|make)\b", re.I), "malware_creation", 0.85),
    (re.compile(r"\b(ddos|ddos attack)\b.*\b(run|launch|perform|target)\b", re.I), "ddos", 0.75),
    (re.compile(r"\b(phish(ing)? (page|site|email|kit))\b", re.I), "phishing", 0.85),
    (re.compile(r"\b(bomb threat|ancaman bom)\b", re.I), "threat", 0.9),
    (re.compile(r"\b(hack(ed)? (into|account|email|facebook|instagram))\b.*\b(help|how|cara)\b", re.I), "unauthorized_access", 0.7),
    (re.compile(r"\b(beli|jual|buy|sell)\b.*\b(narkoba|narkotika|drugs|shabu|meth|cocaine|heroin)\b", re.I), "drugs_trade", 0.8),
]
SAFE_CONTEXT = re.compile(
    r"\b(detect|deteksi|filter|filtering|moderation|moderasi|prevent|mencegah|"
    r"analyz|analisa|analysis|security research|riset keamanan|block|blokir|"
    r"report|lapor|remove|hapus|test|uji|classification|klasifikasi)\b", re.I)
THRESHOLD = 0.7

def check(text):
    flags = []
    risk = 0.0
    for rx, label, sev in PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        # konteks mitigasi keamanan menurunkan skor
        window = text[max(0, m.start() - 120):m.end() + 120]
        adj = sev * (0.35 if SAFE_CONTEXT.search(window) else 1.0)
        risk = max(risk, adj)
        if adj >= THRESHOLD * 0.6:
            flags.append({"category": label, "risk": round(adj, 2)})
    flags.sort(key=lambda f: -f["risk"])
    return {"safe": risk < THRESHOLD, "risk": round(risk, 2), "flags": flags}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "harmonika-safety",
                        "mode": "heuristic_v1", "threshold": THRESHOLD})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/check":
            self._json({"error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2 * 1024 * 1024:
            self._json({"error": "body_too_large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return
        text = data.get("text")
        if not isinstance(text, str) or not text:
            self._json({"error": "text required"}, 400)
            return
        self._json(check(text[:100000]))

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("ContentSafety on 127.0.0.1:17903", flush=True)
ThreadingHTTPServer(("127.0.0.1", 17903), Handler).serve_forever()
