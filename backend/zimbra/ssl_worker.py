#!/usr/bin/env python3
"""Scoped on-host Zimbra renewal worker. No credentials or model calls.

Activation requires an operator-owned configuration and a successful staging
receipt. stdout and SQLite contain mechanics only, never subprocess output.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import smtplib
import socket
import sqlite3
import ssl
import stat
import subprocess
import tempfile
import time

VERSION = '1.3.0'
PORTS = (443, 25, 587, 465, 993, 7071)
COMMERCIAL = Path('/opt/zimbra/ssl/zimbra/commercial')
STATE = Path('/var/lib/harmonika/zimbra-ssl')
HOOK_PROOF = '''from certbot._internal import renewal,cli
from certbot import configuration
from certbot._internal.plugins import disco
ns=cli.prepare_and_parse_args(disco.PluginsRegistry.find_all(), ['renew','--cert-name','mail.example.test','--non-interactive','--no-directory-hooks','--pre-hook','/bin/true','--post-hook','/bin/true','--deploy-hook','/bin/true'])
c=configuration.NamespaceConfig(ns)
params={k:'/bin/false' for k in ['pre_hook','post_hook','renew_hook']}
renewal.restore_required_config_elements(c,params)
print(int(all(getattr(c.namespace,k,None)=='/bin/true' and cli.set_by_cli(k) for k in params) and c.namespace.directory_hooks is False))
'''


class Blocked(Exception):
    pass


class Failed(Exception):
    pass


class Uncertain(Exception):
    pass


def name(value):
    if (not isinstance(value, str) or not 3 <= len(value) <= 128 or '.' not in value
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x)
                   for x in value.split('.'))):
        raise Blocked('invalid_hostname')
    return value


def san_name(value):
    value=value.lower()
    if value.startswith('*.'):name(value[2:]);return value
    return name(value)


def match_hostname(decoded, hostname):
    """DNS SAN only; a wildcard matches exactly one complete leftmost label."""
    hostname=name(hostname)
    for kind,value in decoded.get('subjectAltName',()):
        if kind!='DNS':continue
        value=san_name(value)
        if value==hostname:return
        if value.startswith('*.') and hostname.count('.')==value.count('.') and hostname.endswith(value[1:]):return
    raise Blocked('hostname_mismatch')


def validate_config(data):
    if not isinstance(data, dict) or set(data) != {'cert_name', 'hostname', 'service_control_approved'}:
        raise Blocked('invalid_config')
    name(data['cert_name']); name(data['hostname'])
    if type(data['service_control_approved']) is not bool:
        raise Blocked('invalid_approval')
    return data


def read_regular(path, limit=65536, key=False, owners=(0,)):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid not in owners or s.st_size > limit
                or s.st_mode & (0o037 if key else 0o022) or s.st_nlink != 1):
            raise Blocked('unsafe_file')
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise Blocked('file_limit')
        return data
    finally:
        os.close(fd)


def secure_directory(path):
    """Reject writable/symlink ancestors before creating a root-private leaf."""
    for parent in reversed([path, *path.parents]):
        if not parent.exists():
            parent.mkdir(mode=0o700)
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise Blocked('unsafe_directory')
    os.chmod(path, 0o700)


def put(path, data, uid=0, gid=0, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.ssl-', dir=path.parent)
    try:
        os.fchmod(fd, mode); os.fchown(fd, uid, gid)
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def command(argv, timeout=20, data=None, mutating=False):
    # Temporary streams bound RAM and never expose raw provider/vendor output.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            p = subprocess.run(argv, input=data, stdout=out, stderr=err, timeout=timeout,
                               env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C'})
        except subprocess.TimeoutExpired:
            raise Uncertain('command_timeout') if mutating else Failed('probe_timeout')
        out.seek(0); err.seek(0)
        body, errors = out.read(65537), err.read(65537)
        if len(body) > 65536 or len(errors) > 65536:
            raise Uncertain('output_limit') if mutating else Failed('output_limit')
        if p.returncode or (mutating and re.search(rb'\bfailed\b|\berror:', body + errors, re.I)):
            raise Failed('command_failed')
        return body


def certificate(path):
    decoded = ssl._ssl._test_decode_cert(str(path))
    sans = sorted({san_name(v) for k, v in decoded.get('subjectAltName', ()) if k == 'DNS'})
    if not sans:
        raise Blocked('missing_san')
    der = command(['/usr/bin/openssl', 'x509', '-in', str(path), '-outform', 'DER'])
    return {'fingerprint': hashlib.sha256(der).hexdigest(), 'sans': sans,
            'expires': ssl.cert_time_to_seconds(decoded['notAfter'])}


def chain_blocks(data):
    blocks = re.findall(rb'-----BEGIN CERTIFICATE-----\s+.+?-----END CERTIFICATE-----', data, re.S)
    if not blocks or len(blocks) > 8:
        raise Blocked('invalid_chain')
    return blocks


def verify_material(key, cert, chain, hostname, baseline_sans, minimum_days=7):
    info = certificate(cert)
    if info['expires'] < time.time() + minimum_days * 86400 or not set(baseline_sans) <= set(info['sans']):
        raise Blocked('expiry_or_san_changed')
    # OS trust + DNS name, never accept a staging or self-signed production cert.
    match_hostname(ssl._ssl._test_decode_cert(str(cert)), hostname)
    command(['/usr/bin/openssl', 'verify', '-purpose', 'sslserver',
             '-CAfile', '/etc/ssl/certs/ca-certificates.crt', '-untrusted', str(chain), str(cert)])
    pub = command(['/usr/bin/openssl', 'x509', '-in', str(cert), '-pubkey', '-noout'])
    cert_spki = command(['/usr/bin/openssl', 'pkey', '-pubin', '-outform', 'DER'], data=pub)
    key_spki = command(['/usr/bin/openssl', 'pkey', '-in', str(key), '-pubout', '-outform', 'DER'])
    if hashlib.sha256(cert_spki).digest() != hashlib.sha256(key_spki).digest():
        raise Blocked('key_mismatch')
    return info


def served_fingerprints(hostname):
    result = {}
    context = ssl.create_default_context()
    for port in PORTS:
        if port in (25, 587):
            with smtplib.SMTP('127.0.0.1', port, timeout=15) as client:
                client._host = hostname
                if client.ehlo()[0] != 250 or not client.has_extn('starttls'):
                    raise Failed('smtp_starttls_unavailable')
                client.starttls(context=context)
                if client.ehlo()[0] != 250: raise Failed('smtp_post_tls_ehlo')
                result[port] = hashlib.sha256(client.sock.getpeercert(binary_form=True)).hexdigest()
        else:
            with socket.create_connection(('127.0.0.1', port), 8) as raw:
                with context.wrap_socket(raw, server_hostname=hostname) as client:
                    result[port] = hashlib.sha256(client.getpeercert(binary_form=True)).hexdigest()
    return result


def inspect_config(cert_name):
    path = Path('/etc/letsencrypt/renewal') / (cert_name + '.conf')
    body = read_regular(path)
    global_body = read_regular(Path('/etc/letsencrypt/cli.ini')) if Path('/etc/letsencrypt/cli.ini').exists() else b''
    selected = {}
    # ConfigObj has root fields before [renewalparams]. Never print their text.
    for candidate in (global_body, body):
        section = 'root'; seen = set()
        for raw in candidate.decode().splitlines():
            line = raw.strip()
            if not line or line.startswith(('#', ';')): continue
            if line.startswith('['): section = line.strip('[] '); continue
            if '=' not in line or section not in ('root', 'renewalparams'): continue
            k, v = (p.strip() for p in line.split('=', 1)); k = k.replace('-', '_')
            if k in ('authenticator', 'installer', 'server', 'renew_before_expiry'):
                if (section, k) in seen: raise Blocked('ambiguous_config')
                seen.add((section, k)); selected[k] = v
            if k in ('cert', 'chain', 'fullchain', 'privkey', 'archive_dir'):
                expected = '/etc/letsencrypt/archive/' + cert_name if k == 'archive_dir' else '/etc/letsencrypt/live/' + cert_name + '/' + k + '.pem'
                if v != expected: raise Blocked('configured_lineage_path_mismatch')
    if selected.get('authenticator') != 'standalone' or selected.get('installer', 'None') not in ('None', 'none', ''):
        raise Blocked('unsupported_challenge')
    if selected.get('server', 'https://acme-v02.api.letsencrypt.org/directory') != 'https://acme-v02.api.letsencrypt.org/directory':
        raise Blocked('production_ca_required')
    if selected.get('renew_before_expiry', '30 days') != '30 days':
        raise Blocked('renewal_policy_requires_review')
    help_text = command(['/usr/bin/certbot', '--help', 'all'])
    if b'--no-directory-hooks' not in help_text: raise Blocked('hook_isolation_unavailable')
    if command(['/usr/bin/python3', '-I', '-c', HOOK_PROOF]).strip() != b'1':
        raise Blocked('config_hook_override_unproven')
    return hashlib.sha256(body + b'\0' + global_body).hexdigest()


class Worker:
    def __init__(self, config):
        self.config = validate_config(config)
        self.directory = STATE / config['cert_name']
        self.marker = self.directory / 'reconciliation-required.json'
        self.receipt = self.directory / 'staging.json'
        self.zimbra = pwd.getpwnam('zimbra')
        self.transaction = False

    def approved(self):
        if not self.config['service_control_approved']: raise Blocked('service_control_approval_required')

    def mutate(self, argv, timeout=120):
        self.approved()
        put(self.marker, b'{"state":"reconciliation_required"}\n')
        try:
            result = command(argv, timeout=timeout, mutating=True)
        except Failed:
            if not self.transaction: self.marker.unlink()
            raise
        if not self.transaction: self.marker.unlink()
        return result

    def vendor(self, binary, *args, mutation=False):
        if binary not in ('zmproxyctl', 'zmcertmgr', 'zmcontrol'): raise Blocked('invalid_controller')
        argv = ['/usr/bin/su', '-', 'zimbra', '-c', shlex.join(['/opt/zimbra/bin/' + binary, *map(str, args)])]
        result = self.mutate(argv, 480) if mutation else command(argv, timeout=30)
        if re.search(rb'\bfailed\b|\berror:', result, re.I): raise Failed('vendor_reported_failure')
        return result

    def source(self):
        root = Path('/etc/letsencrypt/live') / self.config['cert_name']
        archive = Path('/etc/letsencrypt/archive') / self.config['cert_name']
        paths = {}
        versions = set()
        for kind in ('cert', 'chain', 'privkey'):
            link = root / (kind + '.pem')
            if not link.is_symlink(): raise Blocked('lineage_layout_changed')
            path = link.resolve(strict=True)
            m = re.fullmatch(kind + r'(\d+)\.pem', path.name)
            if path.parent != archive or not m: raise Blocked('lineage_escape')
            versions.add(m[1])
            s = path.lstat()
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & (0o077 if kind == 'privkey' else 0o022)):
                raise Blocked('unsafe_lineage_file')
            paths[kind] = path
        if len(versions) != 1: raise Blocked('lineage_version_mismatch')
        return paths

    def snapshot(self):
        digest = inspect_config(self.config['cert_name'])
        paths = self.source(); info = certificate(paths['cert'])
        # Matching primary name is checked through OpenSSL, not substring SANs.
        match_hostname(ssl._ssl._test_decode_cert(str(paths['cert'])), self.config['hostname'])
        info.update(config_sha256=digest, paths=paths,
                    deployed=certificate(COMMERCIAL / 'commercial.crt')['fingerprint'],
                    served=served_fingerprints(self.config['hostname']))
        info['due'] = info['expires'] <= time.time() + 30 * 86400
        return info

    def challenge(self, dry_run, snapshot):
        self.approved()
        status = self.vendor('zmproxyctl', 'status')
        if not re.search(rb'\bis running\b', status, re.I): raise Blocked('proxy_not_healthy')
        args = ['/usr/bin/certbot', 'renew', '--cert-name', self.config['cert_name'], '--non-interactive',
                '--no-directory-hooks', '--pre-hook', '/bin/true', '--post-hook', '/bin/true',
                '--deploy-hook', '/bin/true']
        if b'--no-random-sleep-on-renew' in command(['/usr/bin/certbot', '--help', 'all']):
            args.append('--no-random-sleep-on-renew')
        if dry_run: args.append('--dry-run')
        # Config was checked immediately before; a config command hook blocks.
        if inspect_config(self.config['cert_name']) != snapshot['config_sha256']:
            raise Blocked('config_changed')
        try:
            self.vendor('zmproxyctl', 'stop', mutation=True)
            # If another service owns 80, do not run ACME or touch that service.
            try:
                with socket.create_connection(('127.0.0.1', 80), 1):
                    raise Failed('http_port_not_released')
            except OSError: pass
            self.mutate(args, 600)
        except Uncertain:
            # Certbot ownership is unknown. Keep marker; no blind second action.
            raise
        finally:
            if not self.marker.exists():
                try:self.vendor('zmproxyctl', 'start', mutation=True)
                except (Failed,Uncertain):
                    put(self.marker,b'{"state":"proxy_recovery_required"}\n')
                    raise
        current = served_fingerprints(self.config['hostname'])
        if current != snapshot['served']: raise Failed('proxy_recovery_mismatch')
        if dry_run:
            snapshot={**snapshot,'config_sha256':inspect_config(self.config['cert_name'])}
            self.save_receipt(snapshot)

    def save_receipt(self, snapshot):
        put(self.receipt, json.dumps({'at': time.time(), 'config_sha256': snapshot['config_sha256'],
                                     'sans': snapshot['sans'], 'cert_name': self.config['cert_name'],
                                     'worker_version': VERSION}).encode())

    def stage(self, paths, baseline):
        # Zimbra can read its own isolated 0700 staging, never /etc/letsencrypt.
        root = Path('/var/lib/harmonika-zimbra-ssl-staging')
        secure_directory(root)
        directory = Path(tempfile.mkdtemp(prefix='cert-', dir=root))
        os.chown(root, 0, self.zimbra.pw_gid); os.chmod(root, 0o710)
        os.chown(directory, self.zimbra.pw_uid, self.zimbra.pw_gid); os.chmod(directory, 0o700)
        try:
            for kind in ('cert', 'chain', 'privkey'):
                put(directory / (kind + '.pem'), read_regular(paths[kind], key=kind == 'privkey'),
                    self.zimbra.pw_uid, self.zimbra.pw_gid)
            verify_material(directory / 'privkey.pem', directory / 'cert.pem', directory / 'chain.pem',
                            self.config['hostname'], baseline)
            # Select an installed trusted ISRG root by actual chain validation.
            chain = read_regular(directory / 'chain.pem', owners=(self.zimbra.pw_uid,))
            for root_name in ('ISRG_Root_X1.pem', 'ISRG_Root_X2.pem'):
                root_cert = Path('/etc/ssl/certs') / root_name
                if not root_cert.exists(): continue
                try:
                    command(['/usr/bin/openssl', 'verify', '-CAfile', str(root_cert), '-untrusted',
                             str(directory / 'chain.pem'), str(directory / 'cert.pem')])
                    chain += b'\n' + root_cert.read_bytes(); break
                except Failed: continue
            else: raise Blocked('trusted_isrg_root_unavailable')
            put(directory / 'ca.pem', chain, self.zimbra.pw_uid, self.zimbra.pw_gid)
            self.vendor('zmcertmgr', 'verifycrt', 'comm', directory / 'privkey.pem', directory / 'cert.pem', directory / 'ca.pem')
            return directory
        except Exception:
            self.clean_stage(directory); raise

    @staticmethod
    def clean_stage(directory):
        for filename in ('privkey.pem', 'cert.pem', 'chain.pem', 'ca.pem'):
            (directory / filename).unlink(missing_ok=True)
        directory.rmdir()

    def deploy(self, paths, baseline):
        self.approved()
        directory = self.stage(paths, baseline)
        backup = Path(tempfile.mkdtemp(prefix='backup-', dir=self.directory))
        os.chmod(backup, 0o700)
        try:
            for filename in ('commercial.key', 'commercial.crt', 'commercial_ca.crt'):
                put(backup / filename, read_regular(COMMERCIAL / filename, key=filename.endswith('.key'),
                                                   owners=(0, self.zimbra.pw_uid)))
            before = certificate(backup / 'commercial.crt')['fingerprint']
            self.transaction = True
            put(self.marker, b'{"state":"deployment_reconciliation_required"}\n')
            try:
                put(COMMERCIAL / 'commercial.key', read_regular(directory / 'privkey.pem', key=True,
                    owners=(self.zimbra.pw_uid,)), self.zimbra.pw_uid, self.zimbra.pw_gid, 0o640)
                self.vendor('zmcertmgr', 'deploycrt', 'comm', directory / 'cert.pem', directory / 'ca.pem', mutation=True)
                self.vendor('zmcontrol', 'restart', mutation=True)
                self.verify_served(certificate(paths['cert'])['fingerprint'])
                self.marker.unlink()
            except Uncertain:
                raise
            except (Failed, OSError, ssl.SSLError, smtplib.SMTPException):
                # Confirmed failure: restore through the vendor, not PID edits.
                put(COMMERCIAL / 'commercial.key', read_regular(backup / 'commercial.key', key=True),
                    self.zimbra.pw_uid, self.zimbra.pw_gid, 0o640)
                for source, dest in (('commercial.crt', 'cert.pem'), ('commercial_ca.crt', 'ca.pem')):
                    put(directory / dest, read_regular(backup / source), self.zimbra.pw_uid, self.zimbra.pw_gid)
                self.vendor('zmcertmgr', 'deploycrt', 'comm', directory / 'cert.pem', directory / 'ca.pem', mutation=True)
                self.vendor('zmcontrol', 'restart', mutation=True)
                self.verify_served(before)
                self.marker.unlink()
                raise Failed('deployment_rolled_back')
        finally:
            self.transaction = False
            # An uncertain vendor command may still be reading these files.
            if not self.marker.exists(): self.clean_stage(directory)

    def verify_served(self, expected):
        # TLS startup can lag a completed controller. Probe only; never retry a mutation.
        for attempt in range(12):
            try:
                if all(x == expected for x in served_fingerprints(self.config['hostname']).values()): return
            except (OSError, Failed, ssl.SSLError, smtplib.SMTPException): pass
            time.sleep(5)
        raise Failed('post_deploy_verification_failed')

    def run(self, mode):
        if mode not in ('inspect', 'dry-run', 'renew'): raise Blocked('invalid_mode')
        if self.marker.exists(): raise Blocked('reconciliation_required')
        before = self.snapshot()
        if mode == 'inspect': return 'inspected'
        unchanged = before['fingerprint'] == before['deployed'] and all(x == before['fingerprint'] for x in before['served'].values())
        if mode == 'renew' and not before['due'] and unchanged: return 'unchanged'
        self.approved()
        if mode == 'dry-run':
            self.challenge(True, before); return 'staging_verified_no_deploy'
        receipt = json.loads(read_regular(self.receipt, 4096))
        if (receipt.get('cert_name') != self.config['cert_name'] or receipt.get('sans') != before['sans']
                or receipt.get('config_sha256') != before['config_sha256'] or receipt.get('worker_version') != VERSION
                or time.time() < receipt.get('at', float('inf'))):
            raise Blocked('staging_receipt_required')
        if before['fingerprint'] == before['deployed'] and not unchanged:
            raise Blocked('endpoint_drift_requires_diagnosis')
        if before['due']: self.challenge(False, before)
        after = self.snapshot()
        if after['fingerprint'] == after['deployed']:
            if after['fingerprint'] == before['fingerprint'] and before['due']:
                raise Blocked('certbot_did_not_renew')
            return 'unchanged'
        self.deploy(after['paths'], before['sans'])
        # Certbot can persist benign CLI hook overrides in its config. Refresh
        # the binding only after production crypto/deploy/endpoints all passed.
        self.save_receipt(after)
        return 'deployed_verified'


def record(directory, status, started):
    allowed = {'inspected', 'unchanged', 'staging_verified_no_deploy', 'deployed_verified',
               'blocked', 'failed', 'reconciliation_required', 'internal_error'}
    if status not in allowed: status = 'internal_error'
    path = directory / 'mechanics.sqlite3'
    if path.exists(): read_regular(path, 8 * 1024 * 1024, key=True)
    connection = sqlite3.connect(path)
    try:
        os.chmod(path, 0o600)
        connection.execute('CREATE TABLE IF NOT EXISTS events(timestamp TEXT, skill_id TEXT, error_type TEXT, latency REAL)')
        connection.execute('INSERT INTO events VALUES(?,?,?,?)', (dt.datetime.now(dt.timezone.utc).isoformat(),
                           'zimbra.ssl.renew', status, round(time.monotonic() - started, 3)))
        connection.execute('DELETE FROM events WHERE rowid NOT IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 1000)')
        connection.commit()
    finally: connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('inspect', 'dry-run', 'renew'): modes.add_argument('--' + mode, action='store_true')
    args = parser.parse_args(); started = time.monotonic(); status = 'blocked'; code = 2; worker = None
    try:
        if os.geteuid() != 0: raise Blocked('root_required')
        config_path = Path(args.config)
        # Configuration parent chain must be root-owned and non-writable.
        for parent in config_path.parents:
            s = parent.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022: raise Blocked('unsafe_config_parent')
        data = read_regular(config_path, 4096, key=True)
        if stat.S_IMODE(config_path.stat().st_mode) != 0o600: raise Blocked('config_mode')
        worker = Worker(json.loads(data)); secure_directory(worker.directory)
        # Serialize proxy maintenance across all certificate lineages as well as
        # this worker; Certbot's own issuance lock does not protect proxy controls.
        fd = os.open(STATE / 'maintenance.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = worker.run('inspect' if args.inspect else 'dry-run' if args.dry_run else 'renew'); code = 0
        finally: os.close(fd)
    except Blocked: status = 'blocked'
    except Uncertain: status, code = 'reconciliation_required', 3
    except Failed: status, code = 'failed', 4
    except Exception: status, code = 'internal_error', 5
    if worker:
        try: record(worker.directory, status, started)
        except Exception: status, code = 'internal_error', 5
    print(json.dumps({'version': VERSION, 'status': status, 'exit_code': code}))
    return code


if __name__ == '__main__': raise SystemExit(main())
