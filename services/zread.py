"""Harmonika ZRead — GitHub repo reader (shallow clone -> structured digest).
Port 17901. Public repos only.
  GET  /health
  POST /read {"url"} -> {"repo", "default_branch", "languages", "tree",
                         "readme", "key_files", "stats"}
"""
import json, os, re, shutil, subprocess, tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

MAX_TREE = 400
MAX_BLOB = 65536
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__",
             "dist", "build", "target", ".next", "vendor", ".idea", ".vscode"}
KEY_FILES = ["package.json", "go.mod", "Cargo.toml", "pyproject.toml",
             "requirements.txt", "Dockerfile", "docker-compose.yml",
             "Makefile", "main.go", "main.py", "main.js", "index.js",
             "app.py", "server.js", "README.md"]
LANG_MAP = {
    ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".py": "python", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".php": "php",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp",
    ".swift": "swift", ".sh": "shell", ".lua": "lua", ".vue": "vue",
    ".svelte": "svelte", ".css": "css", ".scss": "scss", ".html": "html",
    ".md": "markdown", ".sql": "sql",
}

def parse_repo_url(url):
    m = re.match(
        r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/[^\s]*)?$",
        (url or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)

def read_repo(url):
    parsed = parse_repo_url(url)
    if not parsed:
        return {"error": "invalid_github_url"}
    owner, name = parsed
    tmp = tempfile.mkdtemp(prefix="zread-")
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch",
             f"https://github.com/{owner}/{name}.git", tmp],
            capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            err = (r.stderr or "")[-300:]
            if "not found" in err.lower() or "403" in err:
                return {"error": "repo_not_found_or_private"}
            return {"error": f"clone_failed: {err}"}
        branch = ""
        try:
            branch = subprocess.run(["git", "-C", tmp, "rev-parse",
                                     "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True,
                                    timeout=10).stdout.strip()
        except Exception:
            pass

        tree, langs, total_lines, file_count = [], {}, 0, 0
        key_files = {}
        readme = ""
        for root, dirs, files in os.walk(tmp):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), tmp).replace("\\", "/")
                if rel.startswith(".git/"):
                    continue
                p = os.path.join(root, f)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = 0
                ext = os.path.splitext(f)[1].lower()
                if len(tree) < MAX_TREE:
                    tree.append({"path": rel, "size": size})
                file_count += 1
                if ext in LANG_MAP:
                    langs[LANG_MAP[ext]] = langs.get(LANG_MAP[ext], 0) + 1
                base = os.path.basename(rel)
                if base in KEY_FILES and base not in key_files and size <= MAX_BLOB:
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as fh:
                            key_files[base] = fh.read(MAX_BLOB)
                    except OSError:
                        pass
                if base.lower() in ("readme.md", "readme") and not readme and size <= MAX_BLOB:
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as fh:
                            readme = fh.read(MAX_BLOB)
                    except OSError:
                        pass
                if ext in LANG_MAP and size <= 2 * 1024 * 1024:
                    try:
                        with open(p, "rb") as fh:
                            total_lines += sum(1 for _ in fh)
                    except OSError:
                        pass
        return {
            "repo": f"{owner}/{name}",
            "default_branch": branch,
            "languages": dict(sorted(langs.items(), key=lambda x: -x[1])[:12]),
            "tree": tree,
            "readme": readme[:MAX_BLOB],
            "key_files": key_files,
            "stats": {"files": file_count, "lines": total_lines},
        }
    except subprocess.TimeoutExpired:
        return {"error": "clone_timeout"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "harmonika-zread"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            self._json({"error": "body_too_large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return
        if self.path != "/read":
            self._json({"error": "not found"}, 404)
            return
        result = read_repo(data.get("url"))
        code = 200 if "error" not in result else 422
        self._json(result, code)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("ZRead on 127.0.0.1:17901", flush=True)
ThreadingHTTPServer(("127.0.0.1", 17901), Handler).serve_forever()
