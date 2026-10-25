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
BINARY=Path(os.environ.get('HARMONIKA_BINARY_SOURCE',str(ROOT/'current/harmonika')))
SIGN_JS="""const fs=require('fs'),c=require('crypto');const p=fs.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,fs.readFileSync(process.argv[1])).toString('base64')}));"""

def sha(file):return hashlib.sha256(Path(file).read_bytes()).hexdigest()

def sign(data):
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    return subprocess.run([NODE,'-e',SIGN_JS,str(ROOT/'signing-private.pem')],input=raw,capture_output=True,check=True).stdout

def build(release):
    if (SOURCE/'runtime/tools/coding_catalog.py').read_bytes() != (SOURCE/'backend/coding_runtime/catalog.py').read_bytes():raise ValueError('coding_catalog_mismatch')
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(ROOT,0o700)
    if not (ROOT/'signing-private.pem').exists():
        js="const fs=require('fs'),c=require('crypto');const k=c.generateKeyPairSync('ed25519');fs.writeFileSync(process.argv[1],k.privateKey.export({type:'pkcs8',format:'pem'}),{mode:384,flag:'wx'});fs.writeFileSync(process.argv[2],k.publicKey.export({type:'spki',format:'pem'}),{mode:420,flag:'wx'});"
        subprocess.run([NODE,'-e',js,str(ROOT/'signing-private.pem'),str(ROOT/'trusted-public.pem')],check=True)
    destination=SOURCE/'dist'/release
    destination.mkdir(parents=True,exist_ok=False)
    tools=destination/'tools';tools.mkdir()
    for name in ['mcp-core.cjs','mcp-server.cjs','media_bridge.cjs','project-access.cjs','paths.cjs','transcribe.py','state_store.py','tui_gateway.py','clipboard.py','ai_integration.py','ai_adapters.py','skill_registry.py','skill_executor.py','document_skill_worker.py','skill_cli.py','coding_catalog.py','coding_cli.py','telco_cli.py','telco_catalog.json','legal_cli.py','legal_catalog.json','builder_cli.py','builder_catalog.json','master_cli.py','mobile_cli.py','core_cli.py','seo_network.py','seo_extra.py','seo_visual.py','seo_visual.cjs','seo_skill_worker.py','engineering_evidence.py','engineering_skill_worker.py']:
        shutil.copy2(SOURCE/'runtime/tools'/name,tools/name)
    guard=(SOURCE/'runtime/tools/project-file-guard.cjs').read_text()
    (tools/'project-file-guard.cjs').write_text(guard)
    shutil.copy2(SOURCE/'runtime/protocol-check.cjs',tools/'protocol-check.cjs')
    shutil.copy2(SOURCE/'runtime/harmonika.py',tools/'harmonika.py')
    shutil.copytree(SOURCE/'skills',destination/'skills',ignore=shutil.ignore_patterns('__pycache__','.*','*~','*.orig','*.pyc'))
    # Fail build rather than ship dependency specifications that disagree.
    if (SOURCE/'skills/harmonika-documents/requirements.txt').read_bytes() != (SOURCE/'runtime/documents-requirements.lock').read_bytes():
        raise ValueError('documents_dependency_lock_mismatch')
    shutil.copy2(SOURCE/'README.md',destination/'README.md')
    (tools/'docs').mkdir()
    for name in ('LEGAL-ENGINE.md','LEGAL-AUDIT.md','WEB-BUILDER.md','WEB-BUILDER-AUDIT.md','MASTER-SKILLS.md','MOBILE-BUILDER.md','CORE-FOUNDATION.md'):shutil.copy2(SOURCE/'docs'/name,tools/'docs'/name)
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
