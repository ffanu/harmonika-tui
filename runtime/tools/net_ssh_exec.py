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
import sys


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError:
        print(json.dumps({'ok': False, 'error': 'invalid_request'}))
        return 2

    host = str(request.get('host', ''))
    port = int(request.get('port', 22))
    username = str(request.get('username', ''))
    password = str(request.get('password', ''))
    command = str(request.get('command', ''))
    timeout = max(1, min(int(request.get('timeout', 30)), 120))
    expected = (request.get('hostkey_sha256') or '').strip().lower()
    key_path = str(request.get('key_path') or '')

    if not host or not username or not command:
        print(json.dumps({'ok': False, 'error': 'invalid_request'}))
        return 2
    try:
        import paramiko
    except ImportError:
        print(json.dumps({'ok': False, 'error': 'ssh_client_missing: install paramiko'}))
        return 3

    client = paramiko.SSHClient()
    if expected:
        class Pinned(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                digest = hashlib.sha256(key.asbytes())
                accepted = {digest.hexdigest(),
                            base64.b64encode(digest.digest()).decode().rstrip('=').lower(),
                            'sha256:' + base64.b64encode(digest.digest()).decode().rstrip('=').lower()}
                if expected not in accepted:
                    raise paramiko.SSHException('hostkey_mismatch')
                client._host_keys.add(hostname, key.get_name(), key)
        client.set_missing_host_key_policy(Pinned())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

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
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode('utf-8', 'replace')
        err = stderr.read().decode('utf-8', 'replace')
        code = stdout.channel.recv_exit_status()
        print(json.dumps({'ok': True, 'output': out[-20000:], 'stderr': err[-4000:],
                          'exit_status': code}))
        return 0
    except paramiko.AuthenticationException:
        print(json.dumps({'ok': False, 'error': 'ssh_auth_failed'}))
        return 4
    except paramiko.SSHException as exc:
        message = str(exc)
        code = 'ssh_hostkey_failed' if 'hostkey' in message else 'ssh_failed'
        print(json.dumps({'ok': False, 'error': code + ': ' + message[:160]}))
        return 5
    except Exception as exc:  # socket timeouts, DNS, refused, filtered
        print(json.dumps({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:160]}))
        return 6
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
