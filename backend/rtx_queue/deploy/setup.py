"""First install only, scoped to the private RTX queue service. Run as root on VPS."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess

def main():
    if os.geteuid()!=0:raise RuntimeError('root_required')
    unit=Path('/etc/systemd/system/harmonika-rtx-queue.service')
    config=Path('/etc/harmonika-ai/rtx-queue');client=Path('/etc/harmonika-ai/rtx-queue-client.token')
    if unit.exists() or config.exists() or client.exists():raise RuntimeError('existing_installation_refused')
    source=Path('/opt/harmonika-ai/rtx-queue/backend/rtx_queue/deploy')
    subprocess.run(['/opt/harmonika-ai/rtx-queue-venv/bin/python','-c','import aiohttp,PIL'],check=True)
    sock=socket.socket();sock.bind(('127.0.0.1',18186));sock.close()
    account='harmonika-rtx-queue'
    try:
        user=pwd.getpwnam(account)
        if user.pw_dir!='/var/lib/harmonika-rtx-queue' or user.pw_shell!='/usr/sbin/nologin':raise RuntimeError('unexpected_existing_account')
    except KeyError:subprocess.run(['useradd','--system','--user-group','--home-dir','/var/lib/harmonika-rtx-queue','--shell','/usr/sbin/nologin',account],check=True)
    config.mkdir(mode=0o700)
    token=secrets.token_urlsafe(48);client.write_text(token+'\n');client.chmod(0o600)
    clients={'harmonika-backend':hashlib.sha256(token.encode()).hexdigest()}
    (config/'client-keys.json').write_text(json.dumps(clients)+'\n');(config/'client-keys.json').chmod(0o600)
    shutil.copy2(source/'config.example.json',config/'config.json');(config/'config.json').chmod(0o600)
    shutil.copy2(source/'harmonika-rtx-queue.service',unit);unit.chmod(0o644)
    subprocess.run(['systemd-analyze','verify',str(unit)],check=True)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','harmonika-rtx-queue.service'],check=True)
    print(json.dumps({'installed':True,'service':'harmonika-rtx-queue.service','bind':'127.0.0.1:18186','client_secret':'stored_server_side','existing_chat_services_restarted':False}))
if __name__=='__main__':main()
