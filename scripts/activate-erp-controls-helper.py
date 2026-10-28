#!/usr/bin/env python3
"""Sign and activate only ERP CLI/skill overlay, preserving native binary/config."""
import importlib.util,json,os,shutil,subprocess
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1];ROOT=Path.home()/'.config/harmonika-terminal';RELEASE='2026.10.26.13'
s=importlib.util.spec_from_file_location('launcher',SOURCE/'runtime/harmonika.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with m.installation_lock(ROOT):
 current,manifest=m.active(ROOT);m.verify_files(current,manifest);assert current.name=='2026.10.26.12','unexpected_active_release'
 target=ROOT/'versions'/RELEASE;assert not target.exists(),'release_exists';shutil.copytree(current,target)
 for dest,src in {'tools/erp_cli.py':'runtime/tools/erp_cli.py','skills/harmonika-erp/SKILL.md':'skills/harmonika-erp/SKILL.md'}.items():
  p=target/dest;shutil.copy2(SOURCE/src,p);manifest['files'][dest]={'sha256':m.digest(p)}
 manifest['release']=RELEASE;manifest['release_notes']=['Add supplied-data milestone and issue tracking plus reviewed project control source; no native binary, MCP or credential changes.']
 raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
 js="const f=require('fs'),c=require('crypto'),p=f.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,f.readFileSync(process.argv[1])).toString('base64')}));"
 signed=subprocess.run([m.NODE,'-e',js,str(ROOT/'signing-private.pem')],input=raw,capture_output=True,check=True).stdout
 (target/'release.json').write_bytes(signed);os.chmod(target/'release.json',0o600);checked=m.verify_envelope(signed,ROOT);m.preflight(target,checked)
 assert m.digest(target/'harmonika')==m.digest(current/'harmonika'),'binary_changed'
 try:
  m.atomic_state(ROOT/'previous',current.name);m.switch(RELEASE,ROOT);doctor=m.doctor(ROOT);assert doctor['ok']
 except BaseException:m.switch(current.name,ROOT);raise
 print(json.dumps({'status':'active','release':RELEASE,'signature_verified':True,'native_binary_unchanged':True,'config_unchanged':True,'mcp_tools_added':0,'sessions_restarted':False}))
