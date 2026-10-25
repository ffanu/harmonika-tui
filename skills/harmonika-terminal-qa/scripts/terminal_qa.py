#!/usr/bin/env python3
"""Bounded reference harness; never runs the production application or user commands."""
import argparse
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import sys
import time
import unicodedata

SGR = re.compile(r'\x1b\[[0-9;]*m')
ESCAPE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.')


def clusters(text):
    """Small reference subset, not a full Unicode grapheme implementation."""
    pending = ''
    for char in text:
        if pending and (unicodedata.combining(char) or char in '\ufe0e\ufe0f\u200d'
                        or pending.endswith('\u200d') or '\U0001f3fb' <= char <= '\U0001f3ff'):
            pending += char
        else:
            if pending:
                yield pending
            pending = char
    if pending:
        yield pending


def cluster_width(cluster):
    widths = [2 if unicodedata.east_asian_width(c) in 'WF' else 1
              for c in cluster if not unicodedata.combining(c)
              and c not in '\u200d\ufe0e\ufe0f' and not '\U0001f3fb' <= c <= '\U0001f3ff']
    return max(widths, default=0)


def plain(text):
    return ''.join(c for c in ESCAPE.sub('', text) if c >= ' ' and c != '\x7f')


def cell_width(text):
    return sum(cluster_width(c) for c in clusters(plain(text)))


def clip(text, width):
    """Keep complete reference clusters and SGR; discard other control sequences."""
    width = max(0, min(512, width))
    pieces, used = [], 0
    # Preserve only supported color controls, never arbitrary terminal commands.
    pos = 0
    for match in SGR.finditer(text):
        segment = plain(text[pos:match.start()])
        for cluster in clusters(segment):
            size = cluster_width(cluster)
            if used + size > width:
                return ''.join(pieces) + '\x1b[0m'
            pieces.append(cluster)
            used += size
        pieces.append(match.group())
        pos = match.end()
    for cluster in clusters(plain(text[pos:])):
        size = cluster_width(cluster)
        if used + size > width:
            break
        pieces.append(cluster)
        used += size
    return ''.join(pieces) + '\x1b[0m'


class Model:
    def __init__(self):
        self.draft = ''
        self.focus = 'composer'
        self.active = None
        self.next_id = 0
        self.reply = ''
        self.status = 'idle'

    def event(self, kind, value=None, request_id=None):
        if kind == 'type' and self.focus == 'composer':
            self.draft = (self.draft + value)[-256:]
        elif kind == 'tab':
            self.focus = 'history' if self.focus == 'composer' else 'composer'
        elif kind == 'submit' and self.focus == 'composer' and self.draft:
            self.next_id += 1
            self.active = self.next_id
            self.reply = ''
            self.status = 'streaming'
            self.draft = ''
        elif kind == 'cancel':
            self.active = None
            self.status = 'cancelled'
        elif kind in ('delta', 'done', 'error') and self.active is not None and request_id == self.active:
            if kind == 'delta':
                self.reply = (self.reply + value)[-256:]
            else:
                self.status = 'done' if kind == 'done' else 'error'
                self.active = None

    def snapshot(self, rows=24, cols=80):
        rows, cols = max(1, min(rows, 200)), max(1, min(cols, 512))
        lines = [clip('Harmonika Ai', cols), clip('> ' + self.draft, cols), clip(self.reply, cols)]
        return {'focus': self.focus, 'draft': self.draft, 'active': self.active,
                'reply': self.reply, 'status': self.status, 'rows': rows, 'cols': cols,
                'lines': lines[:rows]}


def reducer_checks():
    model = Model()
    model.event('type', 'first')
    model.event('submit')
    first = model.active
    model.event('delta', 'old', first)
    model.event('cancel')
    model.event('delta', 'late', first)
    assert model.reply == 'old' and model.active is None
    model.event('type', 'second')
    model.event('submit')
    second = model.active
    assert second != first
    model.event('delta', 'wrong', first)
    model.event('done', request_id=first)
    assert model.active == second and model.reply == ''
    model.event('delta', 'ok', second)
    model.event('done', request_id=second)
    model.event('delta', 'late', second)
    assert model.reply == 'ok' and model.status == 'done'
    model.event('tab')
    model.event('type', 'ignored')
    assert model.draft == '' and model.focus == 'history'
    model.event('tab')
    model.event('type', 'retry')
    model.event('submit')
    model.event('error', request_id=model.active)
    assert model.status == 'error' and model.active is None
    return 'passed'


def layout_checks():
    sample = '\x1b[31m界e\u0301👩\u200d💻🙂 text\x1b[0m'
    assert cell_width('界e\u0301👩\u200d💻🙂') == 7
    assert plain(clip('界a', 1)) == ''
    assert plain(clip('界a', 2)) == '界'
    for cols in (1, 2, 3, 4, 8, 20, 80):
        rendered = clip(sample, cols)
        assert cell_width(rendered) <= cols and rendered.endswith('\x1b[0m')
        for rows in (1, 2, 3, 24):
            state = Model().snapshot(rows, cols)
            assert len(state['lines']) <= rows
            assert all(cell_width(line) <= cols for line in state['lines'])
    assert '\x1b[2J' not in clip('x\x1b[2Jy', 8)
    return 'passed'


def pty_checks():
    if os.name != 'posix':
        return {'status': 'skipped', 'reason': 'POSIX PTY unavailable'}
    try:
        import fcntl
        import pty
        import termios
        master, slave = pty.openpty()
    except (ImportError, OSError) as exc:
        return {'status': 'skipped', 'reason': type(exc).__name__}
    process = None
    buffer = b''
    total = 0

    def receive():
        nonlocal buffer, total
        deadline = time.monotonic() + 3
        while b'\n' not in buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([master], [], [], max(0, remaining))[0]:
                raise AssertionError('fixture snapshot timeout')
            chunk = os.read(master, 4096)
            if not chunk:
                raise AssertionError('fixture ended before snapshot')
            total += len(chunk)
            if total > 65536:
                raise AssertionError('fixture output limit')
            buffer += chunk
        line, buffer = buffer.split(b'\n', 1)
        return json.loads(line)

    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).with_name('fixture.py'))],
                                   stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                                   env={'PATH': os.defpath, 'PYTHONIOENCODING': 'utf-8'})
        os.close(slave)
        slave = None
        state = receive()
        assert state['focus'] == 'composer' and state['cols'] == 80
        for byte in b'hello':
            os.write(master, bytes([byte]))
            state = receive()
        assert state['draft'] == 'hello'
        os.write(master, b'\t')
        assert receive()['focus'] == 'history'
        os.write(master, b'x')
        assert receive()['draft'] == 'hello'
        os.write(master, b'\t')
        assert receive()['focus'] == 'composer'
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 2, 7, 0, 0))
        os.write(master, b'\x12')  # refresh after real PTY size change, no injected signal
        state = receive()
        assert (state['rows'], state['cols']) == (2, 7)
        assert state['draft'] == 'hello' and len(state['lines']) <= 2
        assert all(cell_width(line) <= 7 for line in state['lines'])
        os.write(master, b'\r')
        state = receive()
        assert state['active'] == 1 and state['draft'] == ''
        os.write(master, b'\x1b')
        assert receive()['status'] == 'cancelled'
        os.write(master, '界🙂'.encode())
        receive()
        state = receive()
        assert state['draft'] == '界🙂'
        os.write(master, b'\x11')
        assert receive()['event'] == 'exit'
        process.wait(timeout=3)
        assert process.returncode == 0
        return {'status': 'passed', 'keyboard': True, 'resize': True, 'cleanup': True}
    finally:
        if process is not None and process.poll() is None:
            # Only this exact fixed child fixture belongs to this harness.
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        os.close(master)
        if slave is not None:
            os.close(slave)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['self-test'], nargs='?', default='self-test')
    parser.parse_args()
    result = {'scope': 'fixture/reference harness; not production binary', 'network': False}
    try:
        result.update(reducer=reducer_checks(), layout=layout_checks(), pty=pty_checks())
        result['ok'] = True
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        result.update(ok=False, error=type(exc).__name__ + ': ' + str(exc)[:240])
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
