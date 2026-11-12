"""Authenticated loopback coordinator, metadata-only WAL and bounded artifacts."""
import hashlib
import hmac
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading

CONTROL = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('quality_coordinator', CONTROL/'coordinator.py')
coordinator = importlib.util.module_from_spec(spec); spec.loader.exec_module(coordinator)
TOKEN = Path('/etc/harmonika-ai/video-toolkit.token').read_text().strip()
engine = coordinator.Engine('/var/lib/harmonika-high-video', TOKEN)


class Handler(BaseHTTPRequestHandler):
    timeout = 20

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != '/rpc' or not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer '+TOKEN).encode('utf-8')):
            self.send_error(401); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 12*1024**2:
                raise coordinator.Failure('invalid_input')
            data = json.loads(self.rfile.read(length)); action = data.get('action'); owner = data.get('owner')
            if not isinstance(owner, str) or not re.fullmatch('[a-f0-9]{64}', owner):
                raise coordinator.Failure('invalid_owner')
            if action == 'capabilities':
                result = engine.capabilities()
            elif action == 'submit':
                request_id = data.get('request_id')
                if not isinstance(request_id, str) or not re.fullmatch('[a-f0-9]{32}', request_id):
                    raise coordinator.Failure('invalid_request')
                result = engine.submit(owner, request_id, data.get('input'))
            elif action in ('job', 'request', 'download'):
                request = action == 'request'; key = data.get('request_id' if request else 'job_id')
                if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key):
                    raise coordinator.Failure('invalid_request')
                result = engine.row(owner, key, request)
                if action == 'download':
                    if result['state'] != 'completed':
                        raise coordinator.Failure('video_not_ready')
                    offset = data.get('offset', 0)
                    if type(offset) is not int or offset < 0:
                        raise coordinator.Failure('invalid_offset')
                    path = engine.root/key/'result.mp4'; total = path.stat().st_size
                    if offset > total:
                        raise coordinator.Failure('invalid_offset')
                    with path.open('rb') as stream:
                        stream.seek(offset); block = stream.read(512*1024)
                    import base64
                    result = {'ok': True, 'provider': 'Harmonika Ai', 'chunked': True, 'mime_type': 'video/mp4',
                              'offset': offset, 'total_bytes': total, 'sha256': result['result']['sha256'],
                              'duration_seconds': result['result']['duration_seconds'], 'quality': 'high',
                              'data': base64.b64encode(block).decode()}
            else:
                raise coordinator.Failure('invalid_action')
        except Exception as error:
            code = str(error) if isinstance(error, coordinator.Failure) else 'quality_unavailable'
            result = {'ok': False, 'provider': 'Harmonika Ai', 'error': {'code': code}}
        body = json.dumps(result, separators=(',', ':')).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *args):
        pass


if __name__ == '__main__':
    threading.Thread(target=engine.worker, daemon=True).start()
    Server(('127.0.0.1', 18266), Handler).serve_forever()
