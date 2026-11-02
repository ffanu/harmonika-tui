#!/usr/bin/env python3
"""Manual signed SSH-only overlay; never restarts user sessions or daemons."""
import importlib.util, json, os, shutil, subprocess
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]
ROOT=Path.home()/'.config/harmonika-terminal'
RELEASE='2026.10.29.1'

def main():
 spec=importlib.util.spec_from_file_location('routeros_launcher',SOURCE/'runtime/harmonika.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 with m.installation_lock(ROOT):
  current,manifest=m.active(ROOT);m.verify_files(current,manifest)
  assert current.name=='2026.10.28.4','unexpected_active_release'
  assert (ROOT/'signing-private.pem').is_file(),'existing_signing_key_required'
  overlay={'tools/ssh_connector.py':'runtime/tools/ssh_connector.py',
   'tools/remote_connector.py':'runtime/tools/remote_connector.py',
   'tools/ssh-mcp.cjs':'runtime/tools/ssh-mcp.cjs',
   'skills/harmonika-ssh/SKILL.md':'skills/harmonika-ssh/SKILL.md',
   'tools/docs/SSH-ROUTEROS.md':'docs/SSH-ROUTEROS.md'}
  target=ROOT/'versions'/RELEASE;assert not target.exists(),'release_already_exists'
  shutil.copytree(current,target)
  for dest,src in overlay.items():
   p=target/dest;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(SOURCE/src,p)
   manifest['files'][dest]={'sha256':m.digest(p)}
  manifest['release']=RELEASE
  manifest['release_notes']=['Dedicated RouterOS read-only SSH audit, private-file password option, verified hosts; native UI unchanged.']
  raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
  sign="const fs=require('fs'),c=require('crypto'),p=fs.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,fs.readFileSync(process.argv[1])).toString('base64')}));"
  signed=subprocess.run([m.NODE,'-e',sign,str(ROOT/'signing-private.pem')],input=raw,capture_output=True,check=True).stdout
  (target/'release.json').write_bytes(signed);os.chmod(target/'release.json',0o600)
  checked=m.verify_envelope(signed,ROOT);m.preflight(target,checked)
  assert m.digest(target/'harmonika')==m.digest(current/'harmonika'),'native_binary_changed'
  try:
   m.atomic_state(ROOT/'previous',current.name);m.switch(RELEASE,ROOT)
   status=m.doctor(ROOT)
   assert status['ok'] and status['ssh']['configured'] and status['ssh']['runtime_available'], 'doctor_failed'
  except BaseException:
   m.switch(current.name,ROOT);raise
  print(json.dumps({'status':'active','release':RELEASE,'signature':'verified','binary_unchanged':True,'mcp_tools':7,'sessions_restarted':False,'config_changed':False}))

if __name__=='__main__':main()
