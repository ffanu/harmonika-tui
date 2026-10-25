#!/usr/bin/env python3
"""Manual signed helper-only overlay; identical TUI binary/config/session files."""
import hashlib,importlib.util,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path.home()/'.config/harmonika-terminal';RELEASE=sys.argv[1] if len(sys.argv)>1 else '2026.10.25.29';EXPECTED=sys.argv[2] if len(sys.argv)>2 else '2026.10.25.28';SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('harmonika_overlay_launcher',SOURCE/'runtime/harmonika.py');launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
with launcher.installation_lock(ROOT):
 current,manifest=launcher.active(ROOT);launcher.verify_files(current,manifest)
 assert current.name==EXPECTED and launcher.RELEASE_RE.fullmatch(RELEASE),'unexpected_active_release'
 assert (ROOT/'signing-private.pem').is_file(),'existing_signing_key_required'
 target=ROOT/'versions'/RELEASE;assert not target.exists(),'release_already_exists'
 shutil.copytree(current,target)
 for name in ('state_store.py','master_cli.py'):
  shutil.copy2(SOURCE/'runtime/tools'/name,target/'tools'/name)
  manifest['files']['tools/'+name]['sha256']=launcher.digest(target/'tools'/name)
 manifest['release']=RELEASE
 raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
 js="const fs=require('fs'),c=require('crypto'),p=fs.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,fs.readFileSync(process.argv[1])).toString('base64')}));"
 signed=subprocess.run([launcher.NODE,'-e',js,str(ROOT/'signing-private.pem')],input=raw,capture_output=True,check=True).stdout
 (target/'release.json').write_bytes(signed);os.chmod(target/'release.json',0o600)
 checked=launcher.verify_envelope(signed,ROOT);launcher.preflight(target,checked)
 assert launcher.digest(target/'harmonika')==launcher.digest(current/'harmonika')
 launcher.atomic_state(ROOT/'previous',current.name);launcher.switch(RELEASE,ROOT)
 print(json.dumps({'status':'active','release':RELEASE,'previous':current.name,'signed_preflight':'passed','binary_unchanged':True,'session_files_modified':False,'automatic_update':False,'changed_helpers':['state_store.py','master_cli.py']}))
