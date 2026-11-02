#!/usr/bin/env python3
"""CLI for the Harmonika trust & safety engine (offline, defensive only).

Usage: trust_cli.py <operation>  with a JSON object on stdin.
Operations: capabilities | fraud_risk | captcha_audit | privacy_audit | bot_defense_plan
Both `fraud_risk` and `trust.fraud_risk` spellings are accepted.
"""
import importlib.util
import json
import sys
from pathlib import Path

MAX_STDIN = 65536


def _load_engine():
    for candidate in (Path(__file__).with_name('trust_engine.py'),
                      Path(__file__).resolve().parent / 'trust_engine.py'):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location('trust_engine', candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise SystemExit(json.dumps({'ok': False, 'error': 'trust_engine_module_missing'}))


def main(argv):
    if len(argv) != 2:
        print(json.dumps({'ok': False, 'error': 'usage: trust_cli.py <operation>'}))
        return 2
    operation = argv[1].strip()
    if not operation.startswith('trust.'):
        operation = 'trust.' + operation
    raw = sys.stdin.read(MAX_STDIN + 1)
    if len(raw) > MAX_STDIN:
        print(json.dumps({'ok': False, 'error': 'input_byte_limit'}))
        return 2
    try:
        data = json.loads(raw or '{}')
    except ValueError:
        print(json.dumps({'ok': False, 'error': 'invalid_json'}))
        return 2
    if isinstance(data, dict) and set(data) == {'data'} and isinstance(data['data'], dict):
        data = data['data']
    if not isinstance(data, dict):
        print(json.dumps({'ok': False, 'error': 'invalid_data'}))
        return 2
    engine = _load_engine()
    try:
        result = engine.call(operation, data)
    except engine.TrustError as exc:
        print(json.dumps({'ok': False, 'error': exc.code}))
        return 2
    except ValueError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:80]}))
        return 2
    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
