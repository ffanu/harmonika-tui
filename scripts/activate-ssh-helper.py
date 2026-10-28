#!/usr/bin/env python3
"""Manual signed SSH helper overlay. Does not restart or attach any TUI session."""
import importlib.util,json,os,shutil,subprocess
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]
ROOT=Path.home()/'.config/harmonika-terminal'
RELEASE='2026.10.26.6'
spec=importlib.util.spec_from_file_location('ssh_overlay_launcher',SOURCE/'runtime/harmonika.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def atomic(path,data):
    tmp=path.with_name(path.name+'.ssh-new')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()

with m.installation_lock(ROOT):
    current,manifest=m.active(ROOT);m.verify_files(current,manifest)
    assert current.name=='2026.10.26.5','unexpected_active_release'
    assert (ROOT/'signing-private.pem').is_file(),'existing_signing_key_required'
    python=ROOT/'ssh-runtime/bin/python'
    subprocess.run([str(python),'-I','-c','import paramiko;assert paramiko.__version__=="4.0.0"'],check=True,timeout=10)
    configfile=Path.home()/'.config/harmonika/harmonika.json'
    config=json.loads(configfile.read_bytes())
    assert not config['permissions'].get('allowed_tools'),'unexpected_auto_approval'
    config.setdefault('mcp',{})['harmonika-ssh']={'type':'stdio','command':str(m.NODE),'args':[str(ROOT/'current/tools/ssh-mcp.cjs')],'timeout':190}
    encoded=(json.dumps(config,indent=2)+'\n').encode()
    js="const fs=require('fs'),{Ajv2020}=require(process.argv[2]);const v=new Ajv2020({strict:false,validateFormats:false}).compile(JSON.parse(fs.readFileSync(process.argv[1])));if(!v(JSON.parse(fs.readFileSync(0,'utf8'))))process.exit(1);"
    dependency=Path.home()/'.config/harmonika/harmonika-tools/node_modules/ajv/dist/2020'
    subprocess.run([m.NODE,'-e',js,str(SOURCE/'runtime/harmonika-schema.json'),str(dependency)],input=encoded,capture_output=True,check=True,timeout=15)
    target=ROOT/'versions'/RELEASE;assert not target.exists(),'release_already_exists'
    shutil.copytree(current,target)
    overlay={
        'tools/harmonika.py':'runtime/harmonika.py',
        'tools/erp_cli.py':'runtime/tools/erp_cli.py',
        'skills/harmonika-erp/SKILL.md':'skills/harmonika-erp/SKILL.md',
        'tools/project-file-guard.cjs':'runtime/tools/project-file-guard.cjs',
        'tools/ssh-mcp.cjs':'runtime/tools/ssh-mcp.cjs',
        'tools/ssh-protocol-check.cjs':'runtime/tools/ssh-protocol-check.cjs',
        'tools/ssh_connector.py':'runtime/tools/ssh_connector.py',
        'tools/remote_connector.py':'runtime/tools/remote_connector.py',
        'tools/ssh-requirements.lock':'runtime/ssh-requirements.lock',
        'tools/docs/SSH-CONNECTOR-SPEC.txt':'docs/SSH-CONNECTOR-SPEC.txt',
        'skills/harmonika-ssh/SKILL.md':'skills/harmonika-ssh/SKILL.md',
        'skills/harmonika-ssh/manifest.yaml':'skills/harmonika-ssh/manifest.yaml',
    }
    for dest,src in overlay.items():
        p=target/dest;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(SOURCE/src,p)
        manifest['files'][dest]={'sha256':m.digest(p)}
    manifest['release']=RELEASE
    manifest['release_notes']=['ERP planner/generator CLI and skill via existing backend registry; MCP budget and TUI binary unchanged.']
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    sign="const fs=require('fs'),c=require('crypto'),p=fs.readFileSync(0);process.stdout.write(JSON.stringify({payload:p.toString('base64'),signature:c.sign(null,p,fs.readFileSync(process.argv[1])).toString('base64')}));"
    signed=subprocess.run([m.NODE,'-e',sign,str(ROOT/'signing-private.pem')],input=raw,capture_output=True,check=True).stdout
    (target/'release.json').write_bytes(signed);os.chmod(target/'release.json',0o600)
    checked=m.verify_envelope(signed,ROOT);m.preflight(target,checked)
    assert m.digest(target/'harmonika')==m.digest(current/'harmonika'),'native_binary_changed'
    backup=ROOT/('ssh-backup-'+RELEASE);backup.mkdir(mode=0o700)
    originals={}
    for p in [configfile,configfile.parent/'crush.json',ROOT/'launcher.py']:
        originals[p]=p.read_bytes() if p.exists() else None
        if p.exists():shutil.copy2(p,backup/p.name);os.chmod(backup/p.name,0o600)
    try:
        m.atomic_state(ROOT/'previous',current.name)
        m.switch(RELEASE,ROOT)
        atomic(ROOT/'launcher.py',(SOURCE/'runtime/harmonika.py').read_bytes())
        atomic(configfile,encoded)
        atomic(configfile.parent/'crush.json',encoded)
        status=m.doctor(ROOT)
        assert status['ok'] and status['ssh']['configured'] and status['ssh']['runtime_available']
    except BaseException:
        m.switch(current.name,ROOT)
        for p,data in originals.items():
            if data is not None:atomic(p,data)
            elif p.exists():p.unlink()
        raise
    print(json.dumps({'status':'active','release':RELEASE,'signature':'verified','binary_unchanged':True,'mcp_tools':7,'sessions_restarted':False,'auto_approval':False}))
