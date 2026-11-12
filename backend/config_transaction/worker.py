"""Owner-only SSH stdio worker: coordinator on Linux, connector on the caller.

The private SQLite journal contains only system mechanics, never plans/output.
There is no HTTP listener or daemon. A per-endpoint lock spans the transaction.
"""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import sqlite3
import sys
import time
from datetime import datetime, timezone
spec = importlib.util.spec_from_file_location('config_engine', Path(__file__).with_name('engine.py'))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
run, VERSION = engine.run, engine.VERSION


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True) + '\n')
    sys.stdout.flush()


BUFFER = b''


def read(timeout, maximum=131072):
    global BUFFER
    deadline = time.monotonic() + timeout
    while b'\n' not in BUFFER:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin.buffer], [], [], remaining)[0]:
            raise ValueError('protocol_timeout')
        chunk = os.read(sys.stdin.buffer.fileno(), 65536)
        if not chunk:
            raise ValueError('invalid_protocol')
        BUFFER += chunk
        if len(BUFFER) > maximum:
            raise ValueError('invalid_protocol')
    line, BUFFER = BUFFER.split(b'\n', 1)
    return json.loads(line)


def main():
    os.umask(0o077)
    began = time.monotonic()
    root = Path(os.environ.get('HARMONIKA_CONFIG_STATE', '/var/lib/harmonika-config-transactions'))
    db = None
    lock = None
    sequence = 0
    try:
        envelope = read(10)
        if type(envelope) is not dict or set(envelope) != {'input', 'scope_id'}:
            raise ValueError('invalid_protocol')
        if not re.fullmatch('[0-9a-f]{64}', envelope['scope_id']):
            raise ValueError('invalid_protocol')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(str(root / 'mechanics.sqlite'), timeout=.5)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS mechanics(timestamp TEXT NOT NULL, skill_id TEXT NOT NULL, error_type TEXT NOT NULL, latency INTEGER NOT NULL)')
        db.execute('INSERT INTO mechanics VALUES(?,?,?,?)', (datetime.now(timezone.utc).isoformat(), 'network.config', 'started', 0))
        db.commit()
        args = envelope['input']
        if type(args) is dict and args.get('dry_run', True) is False:
            lock = open(root / (envelope['scope_id'] + '.lock'), 'a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                db.execute('INSERT INTO mechanics VALUES(?,?,?,?)',
                           (datetime.now(timezone.utc).isoformat(), 'network.config', 'target_busy',
                            round((time.monotonic() - began) * 1000)))
                db.commit()
                send({'type': 'result', 'result': {'ok': False, 'error': 'target_busy', 'state': 'rejected', 'changes_applied': False}})
                return

        def execute(command, timeout, index, phase):
            nonlocal sequence
            sequence += 1
            send({'type': 'execute', 'id': sequence, 'index': index, 'phase': phase,
                  'command': command, 'timeout': timeout})
            response = read(timeout + 5)
            if type(response) is not dict or response.get('id') != sequence or type(response.get('result')) is not dict:
                raise ValueError('invalid_protocol')
            return response['result']

        result = run(args, execute)
        code = result.get('state', 'unknown')
        db.execute('INSERT INTO mechanics VALUES(?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(), 'network.config', code,
                    round((time.monotonic() - began) * 1000)))
        db.commit()
        result['engine'] = 'harmonika-config-transaction'
        result['engine_version'] = VERSION
        result['coordinator_host'] = 'linux-core'
        send({'type': 'result', 'result': result})
    except Exception:
        send({'type': 'result', 'result': {'ok': False, 'error': 'coordinator_unavailable',
              'state': 'needs_reconciliation' if sequence else 'not_started',
              'changes_applied': bool(sequence), 'safe_to_retry': False}})
    finally:
        if lock:
            lock.close()
        if db:
            db.close()


if __name__ == '__main__':
    if sys.argv[1:] == ['--capabilities']:
        send({'engine': 'harmonika-config-transaction', 'version': VERSION,
              'operations': ['preview', 'execute', 'verify', 'compensate'],
              'default': 'preview', 'executor': 'caller_registered_ssh_connector',
              'lock': 'endpoint_scope', 'transport': 'private_ssh_stdio',
              'command_output_persisted': False})
    else:
        main()
