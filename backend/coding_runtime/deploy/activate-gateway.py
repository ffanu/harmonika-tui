"""Activate only the reviewed chat panel gateway after isolated acceptance."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
from uuid import uuid4
stage=Path('/opt/harmonika-ai/coding-gateway-stage');live=Path('/srv/harmonika-web-chat/current')
result=json.loads(Path('/tmp/harmonika-coding-smoke-result.json').read_text())
assert result['ok'] is True and len(set(result['checks']))>=18,'sandbox_smoke_required'
public=json.loads(Path('/tmp/harmonika-coding-public-smoke-result.json').read_text());assert public['ok'] is True,'public_smoke_required'
for name,wanted in json.loads((stage/'hashes.json').read_text()).items():
    assert hashlib.sha256((live/name).read_bytes()).hexdigest()==wanted,'source_changed_abort_rebase'
changed=['main.go','public_api_docs.go','public_api_docs_test.go','public_ai_coding.go','public_ai_coding_test.go','public_ai_coding_live_test.go']
backup=Path('/var/backups/harmonika-coding')/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]);backup.mkdir(mode=0o700,parents=True)
for n in changed+['harmonika-web']:
    if (live/n).exists():shutil.copy2(live/n,backup/n)
marker=Path('/var/lib/harmonika-coding/ready.json');new=live/'harmonika-web.coding-next';restarted=False
try:
    subprocess.run(['patch','--dry-run','-p1','-i',str(stage/'integration.patch')],cwd=live,check=True)
    subprocess.run(['patch','-p1','-i',str(stage/'integration.patch')],cwd=live,check=True)
    for n in changed:
        if n.startswith('public_ai_coding'):shutil.copy2(stage/n,live/n)
    subprocess.run(['gofmt','-w',*changed],cwd=live,check=True)
    subprocess.run(['go','test','./...'],cwd=live,check=True,timeout=120)
    subprocess.run(['go','build','-o',str(new),'.'],cwd=live,check=True,timeout=120)
    os.chmod(new,0o755);os.replace(new,live/'harmonika-web')
    marker.write_text(json.dumps({'schema_version':1,'release':'2026.10.24.20','skills':18,'verified_at':time.time()}));os.chmod(marker,0o600)
    subprocess.run(['systemctl','restart','harmonika-web-chat-live.service'],check=True);restarted=True
    for _ in range(40):
        try:
            urllib.request.urlopen('http://127.0.0.1:3210/v1/coding/capabilities',timeout=2)
        except urllib.error.HTTPError as e:
            if e.code==401:break
        except OSError:pass
        time.sleep(.25)
    else:raise RuntimeError('gateway_route_health_failed')
    print(json.dumps({'ok':True,'gateway':'harmonika-web-chat-live.service','backup':str(backup),'release':'2026.10.24.20','new_route_requires_auth':True}))
except BaseException:
    marker.unlink(missing_ok=True)
    for n in changed+['harmonika-web']:
        if (backup/n).exists():
            if n=='harmonika-web':
                restore=live/'harmonika-web.rollback-next';shutil.copy2(backup/n,restore);os.replace(restore,live/n)
            else:shutil.copy2(backup/n,live/n)
        elif n in changed:(live/n).unlink(missing_ok=True)
    new.unlink(missing_ok=True)
    if restarted:subprocess.run(['systemctl','restart','harmonika-web-chat-live.service'])
    raise
