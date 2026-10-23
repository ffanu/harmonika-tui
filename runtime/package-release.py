#!/usr/bin/python3
"""Owner-only release builder. Private keys never enter packages/public assets."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

SOURCE=Path(__file__).resolve().parent.parent
ROOT=Path(os.environ.get('HARMONIKA_TERMINAL_HOME',str(Path.home()/'.config/harmonika-terminal')))
NODE=os.environ.get('HARMONIKA_NODE') or shutil.which('node') or str(Path.home()/'.local/node/bin/node')
BINARY=Path(os.environ.get('HARMONIKA_BINARY_SOURCE',str(Path.home()/'.local/node/lib/node_modules/@charmland/crush/bin/crush')))
SIGN_JS="""const fs=require('fs'),c=require('crypto');const p=fs.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,fs.readFileSync(process.argv[1])).toString('base64')}));"""

def sha(file):return hashlib.sha256(Path(file).read_bytes()).hexdigest()

def sign(data):
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    return subprocess.run([NODE,'-e',SIGN_JS,str(ROOT/'signing-private.pem')],input=raw,capture_output=True,check=True).stdout

def build(release):
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(ROOT,0o700)
    if not (ROOT/'signing-private.pem').exists():
        js="const fs=require('fs'),c=require('crypto');const k=c.generateKeyPairSync('ed25519');fs.writeFileSync(process.argv[1],k.privateKey.export({type:'pkcs8',format:'pem'}),{mode:384,flag:'wx'});fs.writeFileSync(process.argv[2],k.publicKey.export({type:'spki',format:'pem'}),{mode:420,flag:'wx'});"
        subprocess.run([NODE,'-e',js,str(ROOT/'signing-private.pem'),str(ROOT/'trusted-public.pem')],check=True)
    destination=SOURCE/'dist'/release
    destination.mkdir(parents=True,exist_ok=False)
    tools=destination/'tools';tools.mkdir()
    for name in ['mcp-core.cjs','mcp-server.cjs','project-access.cjs','paths.cjs','transcribe.py']:
        shutil.copy2(SOURCE/'runtime/tools'/name,tools/name)
    guard=(SOURCE/'runtime/tools/project-file-guard.cjs').read_text()
    (tools/'project-file-guard.cjs').write_text(guard)
    shutil.copy2(SOURCE/'runtime/protocol-check.cjs',tools/'protocol-check.cjs')
    shutil.copy2(SOURCE/'runtime/harmonika.py',tools/'harmonika.py')
    shutil.copytree(SOURCE/'skills',destination/'skills')
    shutil.copy2(SOURCE/'README.md',destination/'README.md')
    shutil.copy2(SOURCE/'UPSTREAM-LICENSE.md',tools/'UPSTREAM-LICENSE.md')
    files={str(p.relative_to(destination)):{'sha256':sha(p)} for p in destination.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    manifest={'release':release,'product':'Harmonika Terminal','upstream_version':'0.98.1','platform':'darwin-arm64',
      'binary_sha256':sha(BINARY),'files':files,'automatic_update':False,'binary_included':False}
    (destination/'release.json').write_bytes(sign(manifest))
    package=SOURCE/'dist'/('harmonika-terminal-'+release+'.zip')
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in [*files,'release.json']:archive.write(destination/name,name)
    catalog={'release':release,'product':'Harmonika Terminal','automatic_update':False,'binary_included':False,
      'package_url':'https://chat.harmonika.id/assets/harmonika-terminal/'+package.name,'package_sha256':sha(package),
      'package_size':package.stat().st_size,'upstream_version':'0.98.1'}
    (SOURCE/'dist/stable.json').write_bytes(sign(catalog))
    print(json.dumps({'package':str(package),'manifest':str(destination/'release.json'),'files':len(files),'release':release}))

if __name__=='__main__':
    release=sys.argv[1]
    spec=importlib.util.spec_from_file_location('launcher',SOURCE/'runtime/harmonika.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    if not m.RELEASE_RE.fullmatch(release):raise ValueError('invalid_release')
    build(release)
