"""Private operator transport for the one explicitly authorized disposable host."""
import base64,hashlib,importlib.util,json,socket,shlex,time
from pathlib import Path
import paramiko
HOST='38.47.176.62';ALIAS='saved/harmonika-linux-exam'
def root_path():return Path(Path('/tmp/harmonika-level12-root.path').read_text().strip())
def connection():
 spec=importlib.util.spec_from_file_location('private_access','/Users/id/.config/harmonika/access/store.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);r=m.get_record(ALIAS)
 assert r['host']==HOST and r['port']==22 and r['username']=='root' and r['status']=='verified'
 t=paramiko.Transport(socket.create_connection((HOST,22),timeout=12));t.start_client(timeout=12);key=t.get_remote_server_key();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
 if fingerprint!=r['hostKey']['fingerprint']:t.close();raise RuntimeError('exam_host_key_changed')
 try:t.auth_password('root',r['password'],fallback=False)
 except Exception:t.close();raise RuntimeError('exam_authentication_failed') from None
 return t

def python(code,value=None,timeout=60):
 t=connection()
 try:
  c=t.open_session(timeout=12);c.settimeout(timeout);c.exec_command('python3 -c '+shlex.quote(code));data=json.dumps(value if value is not None else {}).encode();c.sendall(data);c.shutdown_write();out=bytearray();err=bytearray();deadline=time.monotonic()+timeout
  while not c.exit_status_ready() or c.recv_ready() or c.recv_stderr_ready():
   if c.recv_ready():out.extend(c.recv(32768))
   if c.recv_stderr_ready():err.extend(c.recv_stderr(32768))
   if len(out)>1048576 or len(err)>65536:raise RuntimeError('operator_output_budget')
   if time.monotonic()>deadline:raise RuntimeError('operator_deadline')
   time.sleep(.01)
  status=c.recv_exit_status();c.close()
  if status:raise RuntimeError('remote_operation_failed_'+str(status))
  return json.loads(out)
 finally:t.close()

def main():
 code="""import os,json,platform,hashlib,shutil,subprocess,time
from pathlib import Path
assert os.geteuid()==0 and platform.system()=='Linux'
osrelease=dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line);disk=shutil.disk_usage('/');ports=subprocess.run(['ss','-H','-ltn'],capture_output=True,text=True,check=True).stdout;free=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
print(json.dumps({'system':{'os':osrelease.get('PRETTY_NAME','').strip(chr(34)),'arch':platform.machine(),'cpus':os.cpu_count(),'memory_available_kib':free,'disk_free_gib':round(disk.free/2**30,2),'systemd':subprocess.run(['systemctl','is-system-running'],capture_output=True,text=True).stdout.strip()},'machine_sha256':hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),'ports':ports.splitlines(),'tools':{n:shutil.which(n) for n in ['python3','caddy','nginx','docker','node','curl','tar']},'at':time.time(),'mutations':False}))
"""
 value=python(code);(root_path()/'server-preflight.json').write_text(json.dumps(value,indent=2));print(json.dumps(value))
if __name__=='__main__':main()
