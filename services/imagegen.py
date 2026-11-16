"""Harmonika Image Generation — sd-turbo on RTX.
Windows side, port 18283. Persistent warm worker subprocess (model loads
once), JSON-lines protocol over stdin/stdout.
  GET  /health
  POST /generate {"prompt", "steps"?, "size"?} -> {"png_b64", "took_seconds"}
Auth: Bearer token from HARMONIKA_IMAGEGEN_TOKEN env.
"""
import base64, json, os, subprocess, sys, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

TOKEN = os.environ.get("HARMONIKA_IMAGEGEN_TOKEN", "")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe"
MODEL_DIR = r"C:\harmonika\hf-cache\models--stabilityai--sd-turbo\snapshots\b261bac6fd2cf515557d5d0707481eafa0485ec2"

WORKER_SRC = r'''
import base64, json, os, sys
os.environ["HF_HOME"] = r"C:\harmonika\hf-cache"
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from diffusers import AutoPipelineForText2Image

pipe = AutoPipelineForText2Image.from_pretrained(r"%s", torch_dtype=torch.float16, variant="fp16")
pipe = pipe.to("cuda")
print(json.dumps({"ready": True, "cuda": torch.cuda.is_available()}), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        size = int(req.get("size") or 512)
        if not 256 <= size <= 1024:
            size = 512
        steps = int(req.get("steps") or 4)
        if not 1 <= steps <= 50:
            steps = 4
        with torch.inference_mode():
            img = pipe(prompt=req["prompt"][:1500], num_inference_steps=steps,
                       height=size, width=size, guidance_scale=0.0).images[0]
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        out = {"id": req.get("id"), "ok": True,
               "png_b64": base64.b64encode(buf.getvalue()).decode(),
               "format": "png", "size": size, "steps": steps}
    except Exception as e:
        out = {"id": req.get("id") if "req" in dir() else None,
               "ok": False, "error": str(e)[:400]}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
''' % MODEL_DIR

with open(os.path.join(HERE, "worker.py"), "w") as f:
    f.write(WORKER_SRC)

_proc = None
_proc_lock = threading.Lock()
_pending = {}
_pending_lock = threading.Lock()

def reader_loop(proc):
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            print("worker:", line[:200], flush=True)
            continue
        jid = msg.get("id")
        if jid is None and msg.get("ready"):
            print("imagegen worker ready cuda=%s" % msg.get("cuda"), flush=True)
            continue
        with _pending_lock:
            ev = _pending.pop(str(jid), None)
        if ev:
            _RESULTS[str(jid)] = msg
            ev.set()

_RESULTS = {}

def get_worker():
    global _proc
    with _proc_lock:
        if _proc is None or _proc.poll() is not None:
            _proc = subprocess.Popen(
                [PY, os.path.join(HERE, "worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            threading.Thread(target=reader_loop, args=(_proc,), daemon=True).start()
        return _proc

def generate(prompt, steps, size, timeout=180):
    jid = uuid.uuid4().hex[:16]
    ev = threading.Event()
    with _pending_lock:
        _pending[jid] = ev
    proc = get_worker()
    proc.stdin.write(json.dumps({"id": jid, "prompt": prompt,
                                 "steps": steps, "size": size}) + "\n")
    proc.stdin.flush()
    if not ev.wait(timeout):
        return {"ok": False, "error": "generation_timeout"}
    return _RESULTS.pop(jid, {"ok": False, "error": "result_lost"})

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/health":
            alive = _proc is not None and _proc.poll() is None
            self._json({"ok": True, "service": "harmonika-imagegen",
                        "model": "sd-turbo (RTX)", "worker_alive": alive})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not TOKEN or self.headers.get("Authorization", "") != "Bearer " + TOKEN:
            self._json({"error": "unauthorized"}, 401)
            return
        if self.path != "/generate":
            self._json({"error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            self._json({"error": "prompt required"}, 400)
            return
        try:
            steps = min(max(int(data.get("steps") or 4), 1), 50)
            size = min(max(int(data.get("size") or 512), 256), 1024)
        except Exception:
            steps, size = 4, 512
        t0 = time.time()
        result = generate(prompt.strip(), steps, size)
        result["took_seconds"] = round(time.time() - t0, 1)
        if not result.get("ok"):
            self._json(result, 500)
            return
        self._json(result)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("ImageGen on 127.0.0.1:18283", flush=True)
ThreadingHTTPServer(("127.0.0.1", 18283), Handler).serve_forever()
