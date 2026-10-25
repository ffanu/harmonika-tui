#!/usr/bin/env python3
"""Fixed keyboard fixture. Its stdin/stdout must be the harness-owned PTY."""
import codecs
import json
import os
from pathlib import Path
import runpy
import sys
import termios
import tty

# Isolated Python mode deliberately omits script-directory imports.
Model = runpy.run_path(str(Path(__file__).with_name('terminal_qa.py')))['Model']


def main():
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    model = Model()
    decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def emit(**extra):
        size = os.get_terminal_size(fd)
        value = model.snapshot(size.lines, size.columns)
        value.update(extra)
        sys.stdout.write(json.dumps(value, ensure_ascii=True) + '\n')
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        emit(event='ready')
        for _ in range(512):
            data = os.read(fd, 1)
            if not data:
                return
            if data == b'\x11':
                emit(event='exit')
                return
            if data == b'\t':
                model.event('tab')
            elif data in (b'\r', b'\n'):
                model.event('submit')
            elif data == b'\x1b':
                model.event('cancel')
            elif data == b'\x12':
                pass
            else:
                text = decoder.decode(data)
                if not text:
                    continue
                if text.isprintable():
                    model.event('type', text)
            emit(event='snapshot')
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)


if __name__ == '__main__':
    main()
