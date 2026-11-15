"""Install only the new collector. Existing gateway processes remain untouched."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--python',default='/opt/harmonika-ai/builder-venv/bin/python')
    p.add_argument('--operator-confirmed-current-key-consent',action='store_true')
    args=p.parse_args()
    if os.geteuid()!=0:raise ValueError('root_required')
    package=Path('/opt/harmonika-ai/quality_audit')
    configdir=Path('/etc/harmonika-ai/quality-audit')
    storage=Path('/var/lib/harmonika-quality-audit')
    unit=Path('/etc/systemd/system/harmonika-quality-audit.service')
    if any(path.exists() for path in (package,configdir,storage,unit)):
        raise ValueError('existing_installation_preserved')
    subprocess.run([args.python,'-c','import aiohttp,cryptography'],check=True,capture_output=True)
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',18208))==0:raise ValueError('collector_port_busy')
    for directory in (package,configdir,storage):
        directory.mkdir(mode=0o700);directory.chmod(0o700)
    for name in ('__init__.py','redaction.py','store.py','service.py','operator.py','consent_current.py'):
        target=package/name;shutil.copyfile(Path(__file__).parent/name,target);target.chmod(0o600)
    config={'database':str(storage/'quality.sqlite3'),'port':18208,'retention_days':30,
            'max_events':100000,'max_bytes':536870912}
    for field,size,binary in (('encryption_key_file',32,True),('identity_key_file',32,True),
                              ('ingest_token_file',32,False),('admin_token_file',32,False)):
        path=configdir/(field.removesuffix('_file')+'.key')
        path.write_bytes(os.urandom(size) if binary else secrets.token_hex(size).encode())
        path.chmod(0o600);config[field]=str(path)
    target=configdir/'config.json';target.write_text(json.dumps(config,indent=2));target.chmod(0o600)
    # Operator explicitly confirmed current users' consent. Future keys remain
    # metadata-only until their consent is recorded separately.
    confirmed=[]
    if args.operator_confirmed_current_key_consent:
        query="SELECT id FROM public_ai_tokens WHERE status='active' AND revoked_at IS NULL ORDER BY id"
        rows=subprocess.check_output(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-d','harmonika_web','-Atc',query],text=True)
        confirmed=[int(row) for row in rows.splitlines() if row.strip()]
    sys.path.insert(0,'/opt/harmonika-ai')
    from quality_audit.store import Store
    store=Store(config['database'],Path(config['encryption_key_file']).read_bytes(),Path(config['identity_key_file']).read_bytes())
    for token_id in confirmed:store.consent(token_id,True)
    unit.write_text(f'''[Unit]
Description=Harmonika private per-key quality audit collector
After=network.target
[Service]
Type=simple
User=root
WorkingDirectory=/opt/harmonika-ai
ExecStart={args.python} -m quality_audit.service --config {target}
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
ReadWritePaths={storage}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
MemoryMax=192M
TasksMax=32
Environment=PYTHONDONTWRITEBYTECODE=1
StandardOutput=null
StandardError=null
[Install]
WantedBy=multi-user.target
''');unit.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True)
    subprocess.run(['systemctl','enable','--now','harmonika-quality-audit.service'],check=True,capture_output=True)
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:18208/healthz',timeout=2) as response:
                if json.load(response).get('ok'):break
        except Exception:time.sleep(.25)
    else:raise ValueError('new_collector_health_failed')
    report={'collector':'active','port':18208,'current_keys_with_confirmed_consent':len(confirmed),
            'future_keys_default':'metadata_only','retention_days':30,
            'existing_services_restarted':[], 'gateway_capture':'not_active_until_verified_gateway_activation',
            'source_sha256':{name:hashlib.sha256((package/name).read_bytes()).hexdigest() for name in ('store.py','service.py','redaction.py')}}
    (storage/'installation.json').write_text(json.dumps(report,indent=2));(storage/'installation.json').chmod(0o600)
    print(json.dumps(report))


if __name__=='__main__':main()
