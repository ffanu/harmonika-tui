#!/usr/bin/env python3
"""Run on the engine server. Credentials are read locally, never emitted."""
import argparse,json,pathlib,urllib.request,urllib.error,time
p=argparse.ArgumentParser();p.add_argument('--stage',action='store_true');a=p.parse_args();prefix='stage-' if a.stage else '';port=18232 if a.stage else 18231
base='http://127.0.0.1:'+str(port);config=pathlib.Path('/etc/harmonika-remote');root=pathlib.Path('/var/lib/harmonika-remote-access')/('stage' if a.stage else '')
token=(config/(prefix+'api.token')).read_text().strip();approval=(config/(prefix+'approval.token')).read_text().strip()
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'};results={}
def call(path,body=None,extra=None):
 request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={**headers,**(extra or {})})
 try:
  with urllib.request.urlopen(request,timeout=30) as r:return r.status,json.loads(r.read()) if path!='/metrics' else r.read().decode()
 except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
for _ in range(30):
 try:
  with urllib.request.urlopen(base+'/healthz',timeout=1) as r:assert r.status==200
  break
 except OSError:time.sleep(.5)
else:raise RuntimeError('service_not_ready')
code,body=call('/api/v1/ssh/sessions',{});assert code==200,body;headers['X-Harmonika-Session']=body['session_id']
code,body=call('/api/v1/ssh/test',{'target':'chr'});assert code==200 and body['ok'],body;results['connect']=True
code,body=call('/api/v1/ssh/execute',{'target':'chr','command':'uname -s'});assert code==200 and body['data']['stdout'].strip()=='Linux',body;results['remote_exec']=True
code,body=call('/api/v1/ssh/execute',{'target':'chr','command':'rm -rf /'});assert code==403,body;results['dangerous_command_blocked']=True
code,body=call('/api/v1/ssh/test',{'target':'chr'},extra={'Authorization':'Bearer hai_live_invalid'});assert code==401;results['public_token_blocked']=True
project=root/'projects/smoke';source=project/'upload.txt';dest=project/'received.txt';source.write_text('Harmonika backend own transfer fixture\n');dest.unlink(missing_ok=True)
remote='harmonika-backend-'+('stage' if a.stage else 'prod')+'-'+str(time.time_ns())+'.txt'
args={'target':'chr','project_root':str(project),'local_path':'upload.txt','remote_path':remote}
try:
 code,body=call('/api/v1/ssh/upload',args);assert code==403 and body['error']['code']=='approval_required',body
 code,body=call('/api/v1/ssh/approvals',{'operation':'upload','input':args},{'X-Harmonika-Approval-Key':approval});assert code==200,body;grant=body['approval_token']
 code,up=call('/api/v1/ssh/upload',args,{'X-Harmonika-Approval':grant});assert code==200,up
 code,down=call('/api/v1/ssh/download',{**args,'local_path':'received.txt'});assert code==200,down;assert source.read_bytes()==dest.read_bytes() and up['data']['sha256']==down['data']['sha256'];results['sftp_roundtrip']=True
 code,body=call('/api/v1/ssh/upload',args,{'X-Harmonika-Approval':grant});assert code==403;results['approval_single_use']=True
 code,body=call('/api/v1/ssh/tunnel',{'target':'chr','tunnel':'vnc'});assert code==200 and body['data']['connected'],body;results['ssh_tunnel']=True
 code,body=call('/api/v1/ssh/audit/verify');assert code==200 and body['ok'];results['audit_integrity']=True
 code,body=call('/metrics');assert code==200 and 'harmonika_ssh_commands_total' in body and 'root@' not in body and 'upload.txt' not in body;results['metrics_redacted']=True
finally:
 # Remove only this fixture using the registered account; no existing files.
 import sys
 sys.path.insert(0,'/opt/harmonika-ai/remote-access/current')
 from remote_access.connector import ssh
 cfg=ssh.load_config(str(config/(prefix+'targets.json')));target=ssh.target(cfg,'chr')
 with ssh.connection(target) as client:
  ftp=client.open_sftp()
  try:ftp.remove(target['remote_root']+'/'+remote)
  except FileNotFoundError:pass
  ftp.close()
 source.unlink(missing_ok=True);dest.unlink(missing_ok=True)
print(json.dumps({'stage':a.stage,'results':results,'passed':all(results.values()),'fixture_cleaned':True}))
