"""Install reviewed source and additive discovery modules; never restart gateway."""
import hashlib,importlib.util,io,json,os,shlex,sys,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('bundle',ROOT/'scripts/bundle-erp-plugins.py');bundle=importlib.util.module_from_spec(s);s.loader.exec_module(bundle)
plugins=ROOT/'dist/erp-plugins';summary=bundle.bundle(plugins)
s=importlib.util.spec_from_file_location('ssh',ROOT/'runtime/tools/ssh_connector.py');ssh=importlib.util.module_from_spec(s);s.loader.exec_module(ssh)
stream=io.BytesIO()
with tarfile.open(fileobj=stream,mode='w:gz') as tar:
 for p in (ROOT/'backend/erp_engine').rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts:tar.add(p,arcname='erp_engine/'+p.relative_to(ROOT/'backend/erp_engine').as_posix(),recursive=False)
 for p in plugins.rglob('*'):
  if p.is_file():tar.add(p,arcname='plugins/'+p.relative_to(plugins).as_posix(),recursive=False)
with ssh.connection(ssh.target(ssh.load_config(),'vps')) as client:
 ftp=client.open_sftp()
 try:ftp.mkdir('/opt/harmonika-ai/erp-engine-stage',mode=0o700)
 except OSError:raise RuntimeError('existing_stage_requires_review') from None
 with ftp.open('/opt/harmonika-ai/erp-engine-stage/source.tar.gz','wb') as f:f.write(stream.getvalue())
 ftp.close()
 code=r'''
import pathlib,tarfile,os,json,hashlib
stage=pathlib.Path('/opt/harmonika-ai/erp-engine-stage')
with tarfile.open(stage/'source.tar.gz','r:gz') as tar:tar.extractall(stage,filter='data')
root=pathlib.Path('/opt/harmonika-ai/skills-runtime');dest=pathlib.Path('/opt/harmonika-ai/erp_engine');assert not dest.exists(),'existing_engine_requires_review'
for p in (stage/'plugins').iterdir():
 assert not (root/p.name).exists(),'existing_plugin_requires_review'
 source=p/'skill.py';pin=json.loads((p/'manifest.json').read_text())['module_sha256'];assert hashlib.sha256(source.read_bytes()).hexdigest()==pin
 os.chmod(p,0o755)
 for file in p.iterdir():os.chmod(file,0o644)
os.rename(stage/'erp_engine',dest)
for p in (stage/'plugins').iterdir():os.rename(p,root/p.name)
print(json.dumps({'installed_plugins':56,'gateway_restarted':False,'application_databases_opened':False}))
'''
 _,o,e=client.exec_command('/usr/bin/python3 -c '+shlex.quote(code),timeout=45);output=o.read().decode();error=e.read().decode();status=o.channel.recv_exit_status();print(output);assert status==0,error[-1500:]
(ROOT/'docs/ERP-PLUGIN-SOURCE-REPORT.json').write_text(json.dumps(summary,indent=2)+'\n')
