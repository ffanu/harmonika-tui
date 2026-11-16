"""Harmonika ASR — faster-whisper large-v3 on RTX.
Windows side, port 18281. ctranslate2 (C++) releases the GIL during
inference, so the threaded HTTP server stays responsive in-process.
  GET  /health
  POST /transcribe {"content_b64", "filename"?} -> {"text", "language", "duration"}
"""
import base64, json, os, subprocess, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

TOKEN = os.environ.get("HARMONIKA_ASR_TOKEN", "")
MODEL = r"C:\harmonika\hf-cache\models--Systran--faster-whisper-large-v3\snapshots\edaa852ec7e145841d8ffdb056a99866b5f0a478"

_lock = threading.Semaphore(1)
_model = None
_device = None

def _patch_av():
    """faster-whisper passes metadata_errors to av.open; some PyAV builds
    reject it. Wrap to retry without the kwarg."""
    try:
        import av
        orig = av.open
        if getattr(orig, "_harmonika_patched", False):
            return
        def safe_open(*a, **kw):
            try:
                return orig(*a, **kw)
            except TypeError:
                kw.pop("metadata_errors", None)
                return orig(*a, **kw)
        safe_open._harmonika_patched = True
        av.open = safe_open
    except Exception:
        pass

_patch_av()

def setup_env():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        import torch
        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib) and lib not in os.environ.get("PATH", ""):
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass

_GPU_PROBE = r"""
import sys
sys.path.insert(0, r"C:\harmonika\asr")
from faster_whisper import WhisperModel
try:
    WhisperModel(sys.argv[1], device="cuda", compute_type="float16")
    print("GPU_OK")
except Exception as e:
    print("GPU_FAIL", e)
"""

def _write_probe():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gpu_probe.py")
    with open(p, "w") as f:
        f.write(_GPU_PROBE)
    return p

def gpu_available():
    """Probe CUDA init in a sacrificial subprocess (Blackwell driver
    mismatches can segfault the whole process, not raise)."""
    try:
        r = subprocess.run(
            [sys.executable, _write_probe(), MODEL],
            capture_output=True, text=True, timeout=180)
        return "GPU_OK" in (r.stdout or "")
    except Exception:
        return False

def get_model():
    global _model, _device
    if _model is None:
        from faster_whisper import WhisperModel
        if gpu_available():
            try:
                _model = WhisperModel(MODEL, device="cuda", compute_type="float16")
                _device = "cuda"
            except Exception:
                _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
                _device = "cpu"
        else:
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
            _device = "cpu"
        print("whisper loaded on", _device, flush=True)
    return _model

def transcribe(path, language=None):
    setup_env()
    m = get_model()
    kwargs = dict(beam_size=1, vad_filter=True)
    if language:
        kwargs["language"] = language
    segments, info = m.transcribe(path, **kwargs)
    texts = [seg.text for seg in segments]
    return " ".join(texts).strip(), info.language, round(info.duration, 1)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "harmonika-asr",
                        "model": MODEL, "device": _device or "unloaded"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not TOKEN or self.headers.get("Authorization", "") != "Bearer " + TOKEN:
            self._json({"error": "unauthorized"}, 401)
            return
        if self.path != "/transcribe":
            self._json({"error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > 40 * 1024 * 1024:
            self._json({"error": "body_too_large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return
        b64 = data.get("content_b64") or ""
        if not b64:
            self._json({"error": "content_b64 required"}, 400)
            return
        try:
            raw = base64.b64decode(b64)
        except Exception:
            self._json({"error": "invalid_base64"}, 400)
            return
        if len(raw) > 25 * 1024 * 1024:
            self._json({"error": "file_too_large", "max_bytes": 26214400}, 413)
            return
        suffix = os.path.splitext(str(data.get("filename") or ""))[1][:6] or ".wav"
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            with _lock:
                t0 = time.time()
                lang = data.get("language") or None
                if lang and not str(lang)[:8].isalpha():
                    lang = None
                text, detected, dur = transcribe(tmp, lang)
                took = round(time.time() - t0, 1)
            self._json({"text": text[:100000], "language": detected,
                        "duration": dur, "took_seconds": took})
        except Exception as e:
            self._json({"error": f"transcribe_failed: {e}"}, 500)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("ASR on 127.0.0.1:18281", flush=True)
ThreadingHTTPServer(("127.0.0.1", 18281), Handler).serve_forever()
