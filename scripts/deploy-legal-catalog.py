#!/usr/bin/env python3
"""Owner-run, server-local legal-only activation. No other engine is restarted.
Candidate: /opt/harmonika-ai/legal-all-candidate, after mandatory suite + live staging.
Backups are root-only. Never changes AES master key or public/member/API tokens.
"""
import hashlib,json,os,secrets,shutil,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/harmonika-ai');ACTIVE=ROOT/'legal_engine';CANDIDATE=ROOT/'legal-all-candidate'
TOKEN=Path('/etc/harmonika-ai/legal-engine.token');UNIT=Path('/etc/systemd/system/harmonika-legal-engine.service')
SERVICES=('harmonika-web-chat-live.service','harmonika-ai.service','harmonika-coding-runtime.service','harmonika-telco-engine.service')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pids():return {s:subprocess.check_output(['systemctl','show',s,'-p','MainPID','--value'],text=True).strip() for s in SERVICES}
def run(*args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
def check_manifest(folder,manifest):
 for rel,digest in manifest.items():
  if Path(rel).is_absolute() or '..' in Path(rel).parts or sha(folder/rel)!=digest:raise RuntimeError('source_manifest_mismatch')
def check_live():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for _ in range(50):
  try:
   with op.open('http://127.0.0.1:18190/healthz',timeout=2) as r:v=json.load(r)
   if v.get('service')=='harmonika-legal':break
  except Exception:pass
  time.sleep(.2)
 else:raise RuntimeError('legal_health_failed')
 headers={'X-Harmonika-Legal-Secret':TOKEN.read_text().strip(),'X-Harmonika-Principal':'api-token:900000000000400000'}
 with op.open(urllib.request.Request('http://127.0.0.1:18190/capabilities',headers=headers),timeout=5) as r:v=json.load(r)
 if v.get('implemented_handlers')!=99 or len(v.get('skills',[]))!=99 or not all(v['features'].get(k) for k in ('legal_vision','legal_asr','legal_video_visual','legal_ocr')):raise RuntimeError('legal_capability_failed')
 return {'implemented_handlers':v['implemented_handlers'],'readiness':{k:sum(x['readiness']['status']==k for x in v['skills']) for k in ('available','degraded','unavailable')}}
def main():
 if os.geteuid()!=0:raise RuntimeError('root_operator_required')
 baseline=json.loads((ROOT/'legal-source-manifest.json').read_text());check_manifest(ACTIVE,baseline)
 manifest=json.loads((CANDIDATE/'deploy/source-manifest.json').read_text());check_manifest(CANDIDATE,manifest)
 aes=sha(Path('/etc/harmonika-ai/legal-master.key'));before=pids()
 backup=Path('/var/backups/harmonika-legal')/('catalog-all-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,mode=0o700);os.chmod(backup,0o700)
 for src,name in ((TOKEN,'legal-engine.token'),(UNIT,'legal-engine.service'),(ROOT/'legal-source-manifest.json','source-manifest.json')):shutil.copy2(src,backup/name)
 swapped=False
 try:
  ACTIVE.rename(backup/'legal_engine');swapped=True;CANDIDATE.rename(ACTIVE)
  temp=TOKEN.with_name('legal-engine.token.next');temp.write_text(secrets.token_hex(32)+'\n');temp.chmod(0o600);temp.replace(TOKEN)
  shutil.copy2(ACTIVE/'deploy/harmonika-legal-engine.service',UNIT)
  run('systemctl','daemon-reload');run('systemctl','restart','harmonika-legal-media.service');run('systemctl','restart','harmonika-legal-engine.service')
  result=check_live()
  if sha(Path('/etc/harmonika-ai/legal-master.key'))!=aes or before!=pids():raise RuntimeError('unrelated_engine_changed')
  (ROOT/'legal-source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
  report={'status':'activated','backup':str(backup),'source_files_verified':len(manifest),'aes_key_unchanged':True,'other_engine_pids_unchanged':True,**result};(backup/'activation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
 except Exception:
  if swapped:
   if ACTIVE.exists():ACTIVE.rename(backup/'failed_candidate')
   (backup/'legal_engine').rename(ACTIVE)
   shutil.copy2(backup/'legal-engine.token',TOKEN);shutil.copy2(backup/'legal-engine.service',UNIT);run('systemctl','daemon-reload');run('systemctl','restart','harmonika-legal-media.service');run('systemctl','restart','harmonika-legal-engine.service')
  raise RuntimeError('legal_activation_failed_rolled_back') from None
if __name__=='__main__':main()
