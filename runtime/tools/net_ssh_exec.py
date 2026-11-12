#!/usr/bin/env python3
"""Bounded SSH command runner for the Harmonika network tools.

Reads one JSON request from stdin and prints one JSON result. Used when the MCP
interpreter has no paramiko, so the connector keeps working with the bundled
ssh-runtime interpreter. Credentials are provided by the operator's private
network-targets.json; nothing is logged or echoed back.

Optional hostkey_sha256 pins the server key: when present, a mismatching key
aborts instead of trusting any host.
"""
import base64
import hashlib
import json
import errno
import logging
import re
import socket
import sys
import time


def drain_channel(channel, timeout):
    """Drain both SSH streams concurrently; bound memory and wall-clock duration."""
    buffers = [bytearray(), bytearray()]
    limits = [20000, 4000]
    clipped = [False, False]
    deadline = time.monotonic() + timeout
    timed_out = False
    while True:
        for i, ready, receive in [(0, channel.recv_ready, channel.recv),
                                  (1, channel.recv_stderr_ready, channel.recv_stderr)]:
            if ready():
                chunk = receive(16384)
                room = max(0, limits[i] - len(buffers[i]))
                buffers[i].extend(chunk[:room])
                clipped[i] |= len(chunk) > room
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            status = channel.recv_exit_status()
            break
        if time.monotonic() >= deadline:
            channel.close()
            status = None
            timed_out = True
            break
        time.sleep(0.01)
    result = {"ok": status == 0, "exit_status": status,
              "output": buffers[0].decode('utf-8', 'replace'),
              "stderr": buffers[1].decode('utf-8', 'replace'),
              "output_truncated": clipped[0], "stderr_truncated": clipped[1],
              "content_is_untrusted": True}
    result['connection_status'] = 'connected'
    result['execution_state'] = 'completed' if type(status) is int and status >= 0 else 'unknown'
    result['safe_to_retry'] = False  # A repeated command may duplicate side effects.
    if timed_out or status is None or status < 0:
        result['error'] = 'ssh_command_timeout' if timed_out else 'ssh_exit_status_unknown'
        result['remote_process_may_still_run'] = True
    elif status != 0:
        result['error'] = 'ssh_command_failed'
    return result


def invalid_request():
    return {'ok': False, 'error': 'invalid_request',
            'connection_status': 'not_attempted', 'execution_state': 'not_started',
            'remote_process_may_still_run': False, 'safe_to_retry': False}


def parse_request(stream):
    raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('invalid_request')
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError('invalid_request')
    limits = {'host': 253, 'username': 128, 'password': 4096,
              'command': 8192, 'hostkey_sha256': 100, 'key_path': 4096}
    if set(request) - set(limits) - {'port', 'timeout'}:
        raise ValueError('invalid_request')
    for field, limit in limits.items():
        value = request.get(field, '')
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError('invalid_request')
    for field, default, upper in [('port', 22, 65535), ('timeout', 30, 120)]:
        value = request.get(field, default)
        if type(value) is not int or not 1 <= value <= upper:
            raise ValueError('invalid_request')
    if (not re.fullmatch(r'[A-Za-z0-9_.:%-]+', request.get('host', ''))
            or not request.get('username', '').strip()
            or any(c in request.get('username', '') for c in '\r\n')
            or not request.get('command', '').strip()
            or any(c in request.get('key_path', '') for c in '\r\n')):
        raise ValueError('invalid_request')
    expected = request.get('hostkey_sha256', '').strip()
    if expected and not re.fullmatch(r'(?:SHA256:)?[A-Za-z0-9+/=]{1,90}', expected, re.I):
        raise ValueError('invalid_request')
    return request


def failure(code, phase):
    connected = phase != 'connecting'
    return {'ok': False, 'error': code,
            'connection_status': 'connected' if connected else 'failed',
            'execution_state': 'unknown' if connected else 'not_started',
            'remote_process_may_still_run': connected, 'safe_to_retry': False}


def main():
    try:
        request = parse_request(sys.stdin)
    except (ValueError, TypeError, RecursionError):
        print(json.dumps(invalid_request()))
        return 2

    host = str(request.get('host', ''))
    port = int(request.get('port', 22))
    username = str(request.get('username', ''))
    password = str(request.get('password', ''))
    command = str(request.get('command', ''))
    timeout = request.get('timeout', 30)
    expected = (request.get('hostkey_sha256') or '').strip()  # base64 is case sensitive
    key_path = str(request.get('key_path') or '')

    try:
        import paramiko
    except ImportError:
        result = invalid_request()
        result['error'] = 'ssh_client_missing'
        print(json.dumps(result))
        return 3

    # Transport threads must not echo a server banner or exception text to logs.
    logger = logging.getLogger('paramiko')
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    client = paramiko.SSHClient()
    if expected:
        class Pinned(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                digest = hashlib.sha256(key.asbytes())
                hexd = digest.hexdigest()
                b64 = base64.b64encode(digest.digest()).decode().rstrip('=')
                target = expected
                if target[:7].lower() == 'sha256:':
                    target = target[7:]
                ok = (target == b64) or (target.lower() == hexd)
                if not ok:
                    raise paramiko.SSHException('hostkey_mismatch')
                client._host_keys.add(hostname, key.get_name(), key)
        client.set_missing_host_key_policy(Pinned())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    phase = 'connecting'
    try:
        connect_kwargs = dict(hostname=host, port=port, username=username,
                              timeout=min(timeout, 15), banner_timeout=min(timeout, 15),
                              auth_timeout=min(timeout, 15), allow_agent=False)
        if key_path:
            # Key authentication: the private key stays in the operator's file.
            connect_kwargs.update(key_filename=key_path, look_for_keys=False,
                                  passphrase=password or None)
        else:
            connect_kwargs.update(password=password, look_for_keys=False)
        client.connect(**connect_kwargs)
        phase = 'starting_command'
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        phase = 'reading'
        result = drain_channel(stdout.channel, timeout)
        result['hostkey_verification'] = 'pinned' if expected else 'not_configured'
        print(json.dumps(result))
        return 0 if result['ok'] else 7
    except paramiko.AuthenticationException:
        print(json.dumps(failure('ssh_auth_failed', phase)))
        return 4
    except paramiko.SSHException as exc:
        code = 'ssh_hostkey_failed' if 'hostkey' in str(exc) else 'ssh_protocol_failed'
        print(json.dumps(failure(code, phase)))
        return 5
    except (socket.timeout, TimeoutError):
        code = 'ssh_connect_timeout' if phase == 'connecting' else 'ssh_command_transport_timeout'
        print(json.dumps(failure(code, phase)))
        return 6
    except socket.gaierror:
        print(json.dumps(failure('ssh_dns_failed', phase)))
        return 6
    except OSError as exc:
        code = 'ssh_connection_refused' if exc.errno == errno.ECONNREFUSED else 'ssh_transport_failed'
        print(json.dumps(failure(code, phase)))
        return 6
    except Exception:
        print(json.dumps(failure('ssh_client_failed', phase)))
        return 6
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
