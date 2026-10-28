"""Register verified offline snapshots using small directory junctions, no model copies."""
import json,os,subprocess,time
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;ROOT=Path(r'D:\HarmonikaAI\video-toolkit');ENGINES=Path(r'C:\HarmonikaVideo')
def stage(phase,**extra):(CONTROL/'linker-progress.json').write_text(json.dumps({'phase':phase,**extra}))
def junction(folder,target):
 if folder.is_junction():
  if os.path.normcase(os.readlink(folder).removeprefix('\\\\?\\'))==os.path.normcase(str(target)):return
  raise RuntimeError('unexpected_existing_junction')
 if folder.exists():
  backup=folder.with_name(folder.name+'.before-toolkit');assert not backup.exists(),'existing_cache_backup_requires_review';folder.rename(backup)
 stage('junction',folder=folder.name)
 subprocess.run(['cmd','/d','/c','mklink','/J',str(folder),str(target)],check=True,capture_output=True,timeout=20)
def main():
 rows=json.loads((CONTROL/'models.json').read_text());receipt=json.loads((CONTROL/'model-receipt.json').read_text())['verified_files'];supplement=CONTROL/'supplement-receipt.json';receipt+=json.loads(supplement.read_text())['verified_files'] if supplement.exists() else [];verified={(r['relative'],r['sha256']) for r in receipt};ready=[]
 for group in ['wangp','mmaudio','framepack']:
  stage('checking_group',group=group);group_rows=[r for r in rows if r['group']==group]
  def accepted(row):
   pinned=(row['relative'],row['sha256']) in verified
   # Canonical subfolder replaces the identical originally fetched root T5.
   if not pinned and row['repo']=='DeepBeepMeep/Wan2.1' and row['file']=='umt5-xxl/models_t5_umt5-xxl-enc-quanto_int8.safetensors':
    pinned=any(v['repo']==row['repo'] and v['revision']==row['revision'] and v['file']=='models_t5_umt5-xxl-enc-quanto_int8.safetensors' and v['sha256']==row['sha256'] and v['bytes']==row['bytes'] for v in receipt)
   try:return pinned and (ROOT/row['relative']).is_file() and (ROOT/row['relative']).stat().st_size==row['bytes']
   except OSError:return False
  if not all(accepted(r) for r in group_rows):
   (CONTROL/(group+'-models.json')).write_text(json.dumps({'engine':group,'models_sha256_verified':False,'files':len(group_rows),'inference_verified':False}));continue
  if not (ENGINES/group/'source-receipt.json').is_file():continue
  snapshots={}
  for repo,revision in {(r['repo'],r['revision']) for r in group_rows}:
   hub=ROOT/'models/hub'/('models--'+repo.replace('/','--'));ref=hub/'refs/main';ref.parent.mkdir(parents=True,exist_ok=True);ref.write_text(revision);snapshots[repo]=hub/'snapshots'/revision
  app=ENGINES/group/'app'
  if group=='wangp':junction(app/'ckpts',snapshots['DeepBeepMeep/Wan2.1'])
  elif group=='mmaudio':
   for sub in ['weights','ext_weights']:junction(app/sub,snapshots['hkchengrex/MMAudio']/sub)
  else:junction(app/'hf_download',ROOT/'models')
  stage('ready',group=group);(CONTROL/(group+'-models.json')).write_text(json.dumps({'engine':group,'models_sha256_verified':True,'files':len(group_rows),'inference_verified':False},indent=2));ready.append(group)
 print(json.dumps({'models_ready':ready}))
if __name__=='__main__':main()
