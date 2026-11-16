"""Harmonika Video Generation - CogVideoX-2b on RTX.
Windows side, port 18271. Async job API with SUBPROCESS workers
(parent stays responsive during inference):
  GET  /health
  POST /generate {"prompt"} -> {"job": id}
  GET  /status/<id>         -> {"state": "queued|running|done|failed", "error"?}
  GET  /video/<id>          -> mp4 bytes (when done)
Auth: Bearer token from HARMONIKA_VIDEOGEN_TOKEN env.
"""
import json, os, subprocess, threading, time, uuid, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

TOKEN = os.environ.get("HARMONIKA_VIDEOGEN_TOKEN", "")
OUT_DIR = r"C:\harmonika\videos"
MODEL_DIR = r"C:\harmonika\models\CogVideoX-2b"
PY = r"C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT_DIR, exist_ok=True)

WORKER_SRC = r'''
import json, sys, os
prompt = sys.argv[1]
out_path = sys.argv[2]
os.environ["HF_HOME"] = r"C:\harmonika\hf-cache"
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from diffusers import CogVideoXPipeline
from diffusers.utils import export_to_video
pipe = CogVideoXPipeline.from_pretrained(r"%s", torch_dtype=torch.float16)
pipe.enable_sequential_cpu_offload()
pipe.vae.enable_tiling()
with torch.inference_mode():
    video = pipe(prompt=prompt[:1500], num_inference_steps=30,
                 num_frames=49, guidance_scale=6.0).frames[0]
export_to_video(video, out_path, fps=8)
print("WORKER_OK")
''' % MODEL_DIR

with open(os.path.join(HERE, "worker.py"), "w") as f:
    f.write(WORKER_SRC)

jobs = {}
lock = threading.Lock()
slot = threading.Semaphore(1)

def run_job(job_id, prompt):
    with slot:
        with lock:
            jobs[job_id]["state"] = "running"
        path = os.path.join(OUT_DIR, job_id + ".mp4")
        tmp = path + ".tmp.mp4"
        try:
            r = subprocess.run(
                [PY, os.path.join(HERE, "worker.py"), prompt[:1500], tmp],
                capture_output=True, text=True, timeout=45 * 60)
            if r.returncode != 0 or not os.path.exists(tmp):
                with lock:
                    jobs[job_id].update(
                        state="failed",
                        error=(r.stderr or r.stdout or "worker_failed")[-500:])
                return
            os.replace(tmp, path)
            with lock:
                jobs[job_id].update(state="done", path=path,
                                    size=os.path.getsize(path))
        except subprocess.TimeoutExpired:
            with lock:
                jobs[job_id].update(state="failed", error="worker_timeout")
        except Exception as e:
            with lock:
                jobs[job_id].update(state="failed", error=str(e)[:500])

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _auth(self):
        h = self.headers.get("Authorization", "")
        return TOKEN and h == "Bearer " + TOKEN

    def do_GET(self):
        if self.path == "/health":
            with lock:
                n = len(jobs)
            self._json({"ok": True, "service": "harmonika-videogen",
                        "model": "CogVideoX-2b (local)", "jobs": n})
            return
        if not self._auth():
            self._json({"error": "unauthorized"}, 401)
            return
        if self.path.startswith("/status/"):
            jid = self.path.split("/status/", 1)[1].split("?")[0][:64]
            with lock:
                j = dict(jobs.get(jid) or {})
            if not j:
                self._json({"error": "not_found"}, 404)
            else:
                self._json(j)
            return
        if self.path.startswith("/video/"):
            jid = self.path.split("/video/", 1)[1].split("?")[0][:64]
            with lock:
                j = dict(jobs.get(jid) or {})
            if not j or j.get("state") != "done":
                self._json({"error": "not_ready"}, 404)
                return
            try:
                with open(j["path"], "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                self._json({"error": "read_failed"}, 500)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._auth():
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
        jid = uuid.uuid4().hex[:16]
        with lock:
            jobs[jid] = {"state": "queued", "created": int(time.time())}
            if len(jobs) > 200:
                for old in sorted(jobs, key=lambda k: jobs[k]["created"])[:50]:
                    jobs.pop(old, None)
        threading.Thread(target=run_job, args=(jid, prompt.strip()), daemon=True).start()
        self._json({"job": jid})

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("VideoGen (subprocess workers) on 127.0.0.1:18271", flush=True)
ThreadingHTTPServer(("127.0.0.1", 18271), Handler).serve_forever()
