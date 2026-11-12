"""Fixed read-only Linux probe, sent over SSH without writing a remote file.

Only allowlisted state values and aggregated error signatures leave the host.
No raw journal messages, stderr, process arguments or environment are returned.
"""
import datetime
import json
import re
import selectors
import subprocess
import sys
import time

PATTERNS = {
    'PERMISSION_DENIED': r'permission denied|operation not permitted',
    'LOGROTATE_DUPLICATE_ENTRY': r'duplicate log entry',
    'LOGROTATE_INSECURE_PARENT': r'parent directory has insecure permissions',
    'LOGROTATE_DESTINATION_EXISTS': r'destination .* already exists',
    'CONFIG_PARSE_ERROR': r'configuration (?:file )?(?:syntax )?error|syntax error|unknown option',
    'DNS_RESOLUTION_FAILURE': r'temporary failure in name resolution|name or service not known|(?:failed to|could not|cannot) resolve (?:host|name|address)',
    'CONNECTION_REFUSED': r'connection refused',
    'CONNECTION_TIMEOUT': r'connection timed out|connection timeout|connect timeout',
    'NETWORK_UNREACHABLE': r'network is unreachable|no route to host',
    'TLS_CERTIFICATE_FAILURE': r'certificate verify failed|certificate verification failed|tls handshake failed|ssl handshake failed|certificate has expired',
    'AUTHENTICATION_FAILURE': r'authentication failed|authentication failure|auth failed',
    'NO_SPACE_LEFT': r'no space left on device',
    'OUT_OF_MEMORY': r'out of memory|oom-kill|oom killer',
    'ADDRESS_IN_USE': r'address already in use',
    'RESTART_LIMIT': r'start request repeated too quickly',
    'PROCESS_EXIT_FAILURE': r'failed with result|main process exited.*status=[1-9]',
}
STATES = {
    'LoadState': {'loaded', 'not-found', 'bad-setting', 'error', 'masked', 'stub', 'merged'},
    'ActiveState': {'active', 'reloading', 'inactive', 'failed', 'activating', 'deactivating', 'maintenance', 'refreshing'},
    'SubState': {'running', 'dead', 'failed', 'exited', 'start', 'start-pre', 'start-post', 'auto-restart', 'auto-restart-queued', 'stop', 'stop-sigterm', 'stop-sigkill', 'stop-post', 'final-sigterm', 'final-sigkill', 'condition', 'reload', 'reload-signal', 'reload-notify', 'cleaning'},
    'Result': {'success', 'resources', 'timeout', 'exit-code', 'signal', 'core-dump', 'watchdog', 'start-limit-hit', 'oom-kill', 'protocol', 'exec-condition', 'assert', 'dependency'},
}
NUMBERS = ('ExecMainStatus', 'NRestarts')


def bounded_run(argv, timeout=8, limit=1048576):
    """Drain only stdout; discard potentially sensitive stderr. Bound RAM and time."""
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return {'exit_status': None, 'error': 'command_unavailable', 'data': b''}
    data = bytearray()
    error = ''
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout, selectors.EVENT_READ)
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error = 'command_timeout'
                    break
                for key, _ in sel.select(min(remaining, .2)):
                    chunk = key.fileobj.read1(8192)
                    if not chunk:
                        sel.unregister(key.fileobj)
                        continue
                    if len(data) + len(chunk) > limit:
                        error = 'output_limit'
                        break
                    data.extend(chunk)
                if error:
                    break
        if not error:
            try:
                p.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                error = 'command_timeout'
    finally:
        # This is exclusively the fixed diagnostic child spawned above.
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()
    return {'exit_status': p.returncode, 'error': error, 'data': bytes(data)}


def parse_state(data):
    out = {}
    for line in data.decode('utf8', errors='replace').splitlines():
        key, _, value = line.partition('=')
        if key in STATES:
            out[key] = value if value in STATES[key] else 'unknown'
        elif key in NUMBERS and re.fullmatch(r'[0-9]{1,10}', value):
            out[key] = int(value)
    return out


def parse_journal(data, now):
    signatures = {}
    invalid = 0
    entries = 0
    for line in data.splitlines():
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError()
            msg, ts = entry.get('MESSAGE'), entry.get('__REALTIME_TIMESTAMP')
            if not isinstance(msg, str) or not isinstance(ts, str) or not re.fullmatch(r'[0-9]{1,17}', ts):
                raise ValueError()
            stamp = int(ts) / 1000000
            utc = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError, OSError):
            invalid += 1
            continue
        entries += 1
        for name, pattern in PATTERNS.items():
            if not re.search(pattern, msg, re.I):
                continue
            s = signatures.setdefault(name, {'signature': name, 'count': 0, 'first_seen': utc, 'last_seen': utc, 'recent_24h_count': 0})
            s['count'] += 1
            s['first_seen'] = min(s['first_seen'], utc)
            s['last_seen'] = max(s['last_seen'], utc)
            s['recent_24h_count'] += int(0 <= now - stamp <= 86400)
    return {'entries_examined': entries, 'invalid_entries': invalid, 'signatures': list(signatures.values()),
            'interpretation': 'Bounded retained journal evidence only. No matches or recent entries does not prove health, log completeness or recovery. Signatures classify messages, not independently verified root causes.'}


def probe(service, journal_lines=80, runner=bounded_run):
    if not isinstance(service, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,120}\.service', service):
        return {'ok': False, 'error': 'invalid_service'}
    if type(journal_lines) is not int or not 1 <= journal_lines <= 200:
        return {'ok': False, 'error': 'invalid_journal_lines'}
    now = time.time()
    state = runner(['systemctl', 'show', service, '--no-pager'] + ['-p' + k for k in (*STATES, *NUMBERS)], limit=16384)
    journal = runner(['journalctl', '-u', service, '-n', str(journal_lines), '--no-pager', '-o', 'json', '--output-fields=MESSAGE,__REALTIME_TIMESTAMP'])
    parsed = parse_journal(journal['data'], now)
    values = parse_state(state['data'])
    state_ok = (state['exit_status'] == 0 and not state['error']
                and all(k in values and values[k] != 'unknown' for k in STATES)
                and all(k in values for k in NUMBERS))
    journal_ok = journal['exit_status'] == 0 and not journal['error'] and parsed['invalid_entries'] == 0
    return {'ok': bool(state_ok and journal_ok), 'observed_at': datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
            'service': service, 'health_status': 'not_assessed', 'content_is_untrusted': True,
            'collection_status': 'complete' if state_ok and journal_ok else 'incomplete',
            'state': {'ok': state_ok, 'exit_status': state['exit_status'], 'error': state['error'], 'values': values},
            'journal': {'ok': journal_ok, 'exit_status': journal['exit_status'], 'error': journal['error'], 'limit': journal_lines, **parsed}}


if __name__ == '__main__':
    try:
        result = probe(sys.argv[1], int(sys.argv[2]))
    except Exception:
        result = {'ok': False, 'error': 'service_probe_failed', 'collection_status': 'incomplete'}
    print(json.dumps(result))
