#!/usr/bin/env python3
"""Native SDK smoke against synthetic loopback fixtures; never uses owner sessions/keys."""
import argparse
import base64
import collections
import codecs
import re
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import fcntl
import importlib.util
import pty
import select
import struct
import termios


class Fixture(http.server.BaseHTTPRequestHandler):
    mode = 'invalid_tool_arguments'
    calls = collections.Counter()
    tool_seen = False
    step = 0

    def log_message(self, *_):
        pass

    def do_POST(self):
        cls = type(self)
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        cls.calls[cls.mode] += 1
        cls.tool_seen |= any(t.get('function', {}).get('name') == 'harmonika_workflow_phase' for t in body.get('tools', []))
        if cls.mode not in ['workflow', 'partial']:
            encoded = json.dumps({'error': {'code': cls.mode, 'message': 'SYNTHETIC_PRIVATE_SENTINEL'}}).encode()
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '0')
            self.end_headers()
            self.wfile.write(encoded)
            return
        cls.step += 1
        phases = ['plan', 'write', 'install', 'build', 'test']
        if cls.step <= len(phases):
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_phase_' + str(cls.step), 'type': 'function', 'function': {'name': 'harmonika_workflow_phase', 'arguments': json.dumps({'phase': phases[cls.step-1]})}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'role': 'assistant', 'content': 'Synthetic workflow response finished. No build/test execution claimed.'}, 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for data in [{'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}, {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]:
            self.wfile.write(('data: ' + json.dumps(data) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def start(binary, candidate, root, fixture_url):
    for name in ['config', 'project', 'data', 'state', 'cache', 'xdg-config']:
        (root/name).mkdir(parents=True, exist_ok=True)
    config = {'update': 'disable', 'model': 'harmonika/fixture', 'plugins': [{'package': str(candidate)}], 'mcp': {}, 'providers': {'harmonika': {'name': 'Harmonika synthetic fixture', 'package': 'aisdk:@ai-sdk/openai-compatible', 'settings': {'baseURL': fixture_url, 'apiKey': 'synthetic-test-key'}, 'models': {'fixture': {'name': 'Synthetic', 'capabilities': {'tools': True, 'input': ['text'], 'output': ['text']}, 'limit': {'context': 100000, 'output': 10000}, 'cost': {'input': 0, 'output': 0}}}}}}
    (root/'config/harmonika.json').write_text(json.dumps(config))
    (root/'config/cli.json').write_text(json.dumps({'plugins': [{'package': str(candidate)}]}))
    # Explicit clean child environment: no provider secrets, inherited config, proxy or session data.
    env = {k: os.environ[k] for k in ['PATH', 'TMPDIR', 'LANG', 'TERM'] if k in os.environ}
    env.update({'HOME': str(root), 'USERPROFILE': str(root), 'OPENCODE_PASSWORD': 'synthetic-lease', 'HARMONIKA_PASSWORD': 'synthetic-lease', 'OPENCODE_TEST_HOME': str(root), 'OPENCODE_CONFIG_DIR': str(root/'config'), 'HARMONIKA_CONFIG_DIR': str(root/'config'), 'OPENCODE_CONFIG_CONTENT': '{}', 'OPENCODE_DISABLE_MODELS_FETCH': '1', 'OPENCODE_DISABLE_FILEWATCHER': '1', 'OPENCODE_DB': str(root/'native.db'), 'HARMONIKA_DB': str(root/'native.db'), 'XDG_DATA_HOME': str(root/'data'), 'XDG_CONFIG_HOME': str(root/'xdg-config'), 'XDG_STATE_HOME': str(root/'state'), 'XDG_CACHE_HOME': str(root/'cache')})
    proc = subprocess.Popen([str(binary), 'serve', '--stdio', '--port', '0'], cwd=root/'project', env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ready = queue.Queue()
    def drain():
        for line in proc.stdout:
            try:
                data = json.loads(line)
                if 'url' in data:
                    ready.put(data['url'])
            except ValueError:
                pass
    threading.Thread(target=drain, daemon=True).start()
    # Drain diagnostic bytes without storing them.
    threading.Thread(target=lambda: collections.deque(iter(lambda: proc.stderr.read(4096), b''), maxlen=0), daemon=True).start()
    try:
        return proc, ready.get(timeout=40), env
    except queue.Empty:
        proc.stdin.close()
        raise RuntimeError('native_startup_timeout') from None


def api(url, path, value=None):
    req = urllib.request.Request(url.rstrip('/')+path, data=None if value is None else json.dumps(value).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Basic '+base64.b64encode(b'opencode:synthetic-lease').decode()})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Synthetic isolated server only; no owner account or customer payload.
        raise RuntimeError(str(error.code)+': '+error.read(2000).decode()) from None


def rpc(url, project, session):
    query = urllib.parse.urlencode({'location[directory]': str(project)})
    return api(url, '/api/rpc/harmonika.control/get?'+query, {'input': {'sessionID': session}})['output']


def pty_smoke(binary, root, env, url, session=None):
    # Reuse only the repo's VT screen decoder, not its legacy launcher/clipboard tools.
    spec = importlib.util.spec_from_file_location('screen_decoder', Path(__file__).with_name('smoke-native-interaction.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(root/'project')
        args=[str(binary), '--server', url]+(['-s',session] if session else [])
        os.execve(str(binary), args, {**env, 'TERM': 'xterm-256color', 'COLORTERM': 'truecolor'})
    screen = module.MiniTerm(38, 120)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 38, 120, 0, 0))
    decoder=codecs.getincrementaldecoder('utf-8')('replace')
    pending=''
    def pump(seconds=1):
        nonlocal pending
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            if not select.select([master], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            pending+=decoder.decode(chunk)
            last=pending.rfind('\x1b')
            tail=pending[last:] if last>=0 else ''
            incomplete=tail=='\x1b' or bool(re.fullmatch(r'\x1b\[[0-?]*[ -/]*',tail)) or (tail.startswith(('\x1b]', '\x1bP', '\x1b_')) and not ('\x07' in tail or '\x1b\\' in tail))
            split=last if incomplete else len(pending)
            screen.feed(pending[:split].encode())
            pending=pending[split:]
            if b'\x1b[6n' in chunk:
                os.write(master, b'\x1b[1;1R')
            if b'\x1b]10;?' in chunk:
                os.write(master, b'\x1b]10;rgb:ffff/ffff/ffff\x1b\\')
            if b'\x1b]11;?' in chunk:
                os.write(master, b'\x1b]11;rgb:0000/0000/0000\x1b\\')
    def capture(name):
        text = screen.visible().replace(str(root), 'SYNTHETIC_ROOT')
        (root/(name+'.txt')).write_text(text)
        return text
    results=[]
    try:
        pump(3)
        base=capture('session-wide' if session else 'home-wide')
        if not session:
            results.append({'case':'native_home_no_session_no_failed_plugin','ok':'plugin failed' not in base and 'Plugin failed' not in base and 'F10' in base})
            os.write(master,b'/plugins')
            pump(0.3)
            os.write(master,b'\r')
            pump(2)
            plugins=capture('home-plugins')
            # Native /plugins labels a loaded local plugin as "local"; its
            # available "disable" action proves the selected TUI is active.
            results.append({'case':'home_plugins_control_active','ok':'harmonika.control' in plugins and 'harmonika.control.runtime' in plugins and 'disable' in plugins and 'failed' not in plugins.lower()})
            os.write(master,b'\x1b')
            pump(0.3)
            os.write(master,b'\x1b[21~')
            pump(2)
            home_engine=capture('home-f10')
            results.append({'case':'native_home_engine_panel','ok':'Harmonika · Engine' in home_engine and 'Registry' in home_engine})
            os.write(master,b'\x1b')
            pump(0.3)
        else:
            results.append({'case':'native_session_render','ok':'Harmonika' in base and 'Synthetic' in base})
        os.write(master,b'\x1b[19~')
        pump(3)
        wide=capture('f8-wide')
        results.append({'case':('f8_native_rpc_phases' if session else 'f8_home_unobserved_phases'),'ok':all(x in wide for x in (['Tahapan pekerjaan','Dilaporkan agen','Respons selesai'] if session else ['Tahapan pekerjaan','Belum teramati']))})
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH',20,54,0,0))
        screen.resize(20,54)
        pump(2)
        narrow=capture('f8-narrow')
        results.append({'case':'f8_narrow_54x20','ok':'Tahapan pekerjaan' in narrow and 'F8 Job' in narrow})
        os.write(master,b'\x1b[21~')
        pump(2)
        engine=capture('f10-narrow')
        results.append({'case':'f10_narrow','ok':'Registry' in engine and 'F8 Job' in engine})
        os.write(master,b'\x1b[20~')
        pump(1)
        memory=capture('f9-narrow')
        results.append({'case':'f9_narrow','ok':'Catatan teknis' in memory and 'F8 Job' in memory})
        results.append({'case':'no_plugin_runtime_error','ok':not any(x in wide+narrow+engine+memory for x in ['TypeError','SyntaxError','Plugin failed','ReferenceError'])})
        os.write(master,b'\x1b')
        pump(0.3)
        results.append({'case':'f2_binding_preserved','ok':"bind:'f2'" not in (Path(__file__).resolve().parents[1]/'runtime/tui-control/tui.tsx').read_text()})
        os.write(master,b'\x1b')
        pump(0.3)
        try:
            os.write(master,b'\x03')
            pump(0.2)
            os.write(master,b'\x03')
            pump(0.6)
        except OSError:
            pass
    finally:
        # Own PTY child only. Normal native Ctrl+C exit; no process-name matching.
        os.close(master)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if os.waitpid(pid,os.WNOHANG)[0]:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError('own_pty_child_exit_timeout')
    if not session:
        for result in results:
            if not result['case'].startswith(('native_home','home_','f8_home')):
                result['case']='home_'+result['case']
    for result in results:
        print(json.dumps(result),flush=True)
    (root/('pty-results.json' if session else 'pty-home-results.json')).write_text(json.dumps(results,indent=2))
    return results


def run(binary, candidate, root):
    fixture = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    proc = None
    results = []
    try:
        proc, url, env = start(binary, candidate, root, 'http://127.0.0.1:'+str(fixture.server_port)+'/v1')
        (root/'smoke-runtime.json').write_text(json.dumps({'url': url, 'pid': proc.pid, 'env': env}))
        query = urllib.parse.urlencode({'location[directory]': str(root/'project')})
        for mode, expected in [('invalid_tool_arguments', 1), ('tool_secret_reference_required', 1), ('invalid_tools', 1), ('insufficient_quota', 1), ('tool_engine_unavailable', 3), ('tool_engine_timeout', 2), ('workflow', 6)]:
            Fixture.mode, Fixture.step = mode, 0
            session = api(url, '/api/session', {'title': 'Synthetic control smoke', 'location': {'directory': str(root/'project')}, 'model': {'providerID': 'harmonika', 'id': 'fixture'}})['data']['id']
            api(url, '/api/session/'+session+'/prompt', {'text': 'Synthetic metadata smoke only.'})
            deadline = time.monotonic()+65
            state = {}
            while time.monotonic() < deadline:
                state = rpc(url, root/'project', session)
                if state.get('observed') and state.get('status') in ['completed', 'failed', 'interrupted']:
                    break
                time.sleep(0.2)
            result = {'case': mode, 'requests': Fixture.calls[mode], 'expected_requests': expected, 'state': state}
            result['ok'] = Fixture.calls[mode] == expected and state.get('status') == ('completed' if mode == 'workflow' else 'failed')
            if mode == 'workflow':
                result['ok'] &= all(p['status'] == 'declared' for p in state.get('phases', []) if p['id'] != 'finish') and len(state.get('phases', [])) == 6
                (root/'workflow-session.txt').write_text(session)
            result['ok'] &= 'SYNTHETIC_PRIVATE_SENTINEL' not in json.dumps(state)
            results.append(result)
            print(json.dumps(result), flush=True)
        print(json.dumps({'case': 'marker_tool_registered', 'ok': Fixture.tool_seen}), flush=True)
        results.append({'case': 'marker_tool_registered', 'ok': Fixture.tool_seen})
        results.extend(pty_smoke(binary, root, env, url))
        results.extend(pty_smoke(binary, root, env, url, (root/'workflow-session.txt').read_text()))
        (root/'native-results.json').write_text(json.dumps(results, indent=2))
        if os.environ.get('HARMONIKA_SMOKE_HOLD') == '1':
            print(json.dumps({'holding': True, 'url': url, 'root': str(root)}), flush=True)
            try:
                input()
            except EOFError:
                pass
        return all(r['ok'] for r in results)
    finally:
        if proc:
            # End the stdin ownership lease of this test's own server only.
            proc.stdin.close()
            proc.wait(timeout=20)
        fixture.shutdown()
        fixture.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    root = args.root or Path(tempfile.mkdtemp(prefix='harmonika-control-smoke-'))
    raise SystemExit(0 if run(args.binary.resolve(), args.candidate.resolve(), root.resolve()) else 1)
