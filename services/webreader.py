"""Lightweight URL reader — zero dependencies, uses stdlib only."""
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import urllib.request, urllib.error, re, gzip, html as html_module, json

class ReaderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "service": "harmonika-webreader"}).encode())
            return
        
        if parsed.path == "/search":
            params = parse_qs(parsed.query)
            q = params.get("q", [""])[0]
            if not q or len(q) > 400:
                self._json({"error": "invalid query"}, 400)
                return
            try:
                import subprocess as _sp
                from urllib.parse import quote as _q
                out = _sp.run(
                    ["curl", "-s", "--max-time", "15", "-A",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                     "-H", "Accept: text/html",
                     "https://html.duckduckgo.com/html/?q=" + _q(q)],
                    capture_output=True, timeout=20)
                body = out.stdout.decode("utf-8", errors="replace")
                results = []
                for m in re.finditer(
                    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
                    url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if url.startswith("//duckduckgo.com/l/?uddg=") or "/l/?uddg=" in url:
                        try:
                            from urllib.parse import unquote as _uq
                            url = _uq(url.split("uddg=")[1].split("&")[0])
                        except Exception:
                            pass
                    snippet = ""
                    tail = body[m.end():m.end() + 2000]
                    sm = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', tail, re.S)
                    if sm:
                        snippet = re.sub(r"<[^>]+>", "", sm.group(1)).strip()
                    if title and url.startswith("http"):
                        results.append({"title": title[:200], "url": url, "snippet": snippet[:300]})
                    if len(results) >= 8:
                        break
                self._json({"query": q, "results": results})
            except Exception as e:
                self._json({"error": f"search_failed: {e}"}, 502)
            return

        if parsed.path == "/read":
            params = parse_qs(parsed.query)
            url = params.get("url", [""])[0]
            if not url.startswith("http"):
                self._json({"error": "invalid url"}, 400)
                return
            
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; HarmonikaReader/1.0)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "gzip",
                })
                resp = urllib.request.urlopen(req, timeout=15)
                ct = resp.headers.get("Content-Type", "")
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8", errors="replace")
                
                if "html" not in ct.lower() and "<html" not in body[:500].lower():
                    self._json({"url": url, "title": "", "text": body[:50000], "content_type": ct})
                    return
                
                title, text = self._clean(body)
                self._json({"url": url, "title": title, "text": text[:50000], "length": len(text), "content_type": ct})
            except urllib.error.HTTPError as e:
                self._json({"error": f"HTTP {e.code}", "url": url}, e.code)
            except Exception as e:
                self._json({"error": str(e), "url": url}, 500)
            return
        
        self._json({"error": "not found", "endpoints": ["/read?url=...", "/health"]}, 404)
    
    def _clean(self, html):
        html = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', html, flags=re.S|re.I)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.S)
        html = re.sub(r'<(nav|header|footer|aside)[^>]*>.*?</\1>', '', html, flags=re.S|re.I)
        m = re.search(r'<title[^>]*>(.*?)</title>', html, re.S|re.I)
        title = m.group(1).strip() if m else ""
        html = re.sub(r'<(br|/p|/div|/h[1-6]|/li|/tr)[^>]*>', '\n', html, flags=re.I)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = html_module.unescape(text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        return title, text.strip()
    
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
    
    def log_message(self, *a):
        pass

print("WebReader on 127.0.0.1:17896")
HTTPServer(("127.0.0.1", 17896), ReaderHandler).serve_forever()
