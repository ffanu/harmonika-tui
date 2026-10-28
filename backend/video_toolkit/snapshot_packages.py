"""Local dependency audit snapshots, without network calls or credential values."""
import json,os,subprocess,time
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;ENGINES=Path(r'C:\HarmonikaVideo')
def main():
 env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ['TOKEN','PASSWORD','SECRET','API_KEY'])};env.update(PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_NO_INDEX='1')
 for name in ['wangp','framepack','mmaudio']:
  result=subprocess.run([str(ENGINES/name/'.venv/Scripts/python.exe'),'-I','-m','pip','list','--format=json'],capture_output=True,text=True,env=env,timeout=60)
  assert result.returncode==0,'package_snapshot_failed';packages=json.loads(result.stdout)
  assert isinstance(packages,list) and all(set(p)=={'name','version'} for p in packages)
  (ENGINES/name/'package-lock.json').write_text(json.dumps({'engine':name,'timestamp':time.time(),'packages':packages,'purpose':'audit_snapshot_not_an_installer'},indent=2));print(json.dumps({'engine':name,'packages_recorded':len(packages)}),flush=True)
if __name__=='__main__':main()
