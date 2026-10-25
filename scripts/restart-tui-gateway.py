#!/usr/bin/env python3
"""Explicit, scoped restart of the Harmonika loopback Gateway with rollback."""
import argparse
import ast
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

LABEL='id.harmonika.tui.gateway'
class MaintenanceError(ValueError):pass

def checked_run(argv,timeout=15,env=None):
    try:return subprocess.run(argv,capture_output=True,text=True,timeout=timeout,env=env)
    except subprocess.TimeoutExpired:raise MaintenanceError('command_timeout') from None

def connection_states(output):
    return [line[4:] for line in output.splitlines() if line.startswith('TST=') and line[4:]!='LISTEN']

def native_environment(snapshot,directory,base):
    env=dict(base,HARMONIKA_GLOBAL_CONFIG=str(directory),HARMONIKA_GLOBAL_DATA=str(Path(directory).parent/'global-data'))
    for node in ast.walk(ast.parse(snapshot)):
        if isinstance(node,ast.keyword) and node.arg:
            if node.arg.endswith('_GLOBAL_CONFIG'):env[node.arg]=str(directory)
            elif node.arg.endswith('_GLOBAL_DATA'):env[node.arg]=str(Path(directory).parent/'global-data')
    return env

class System:
    def __init__(self,target):
        self.root=Path.home()/'.config/harmonika-terminal'
        self.target=target or (self.root/'current').resolve(strict=True).name
        self.config=Path.home()/'.config/harmonika'
        self.launcher=Path.home()/'.local/bin/harmonika'
        self.service='gui/'+str(os.getuid())+'/'+LABEL
        self.plist=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
        spec=importlib.util.spec_from_file_location('gateway_owner_launcher',self.root/'launcher.py')
        self.runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.runtime)
        self.gateway=None;self.key=None;self.port=None;self.last_checks={}
    def pid(self):
        result=checked_run(['launchctl','print',self.service])
        match=re.search(r'\bpid = (\d+)',result.stdout)
        return int(match.group(1)) if result.returncode==0 and match else None
    def running_release(self):
        pid=self.pid()
        if not pid:raise MaintenanceError('gateway_not_running')
        command=checked_run(['ps','-p',str(pid),'-o','command='])
        match=re.search(r'/versions/([0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]+)/tools/tui_gateway\.py\s+serve(?:\s|$)',command.stdout)
        if not match:raise MaintenanceError('gateway_process_identity_unknown')
        return match.group(1)
    def verify_release(self,release):
        if not self.runtime.RELEASE_RE.fullmatch(release):raise MaintenanceError('invalid_release')
        directory=self.root/'versions'/release
        if directory.is_symlink() or directory.resolve().parent!=(self.root/'versions').resolve():raise MaintenanceError('invalid_release_path')
        manifest=self.runtime.verify_envelope((directory/'release.json').read_bytes(),self.root)
        if manifest.get('release')!=release:raise MaintenanceError('release_identity_mismatch')
        self.runtime.preflight(directory,manifest)
        return directory,manifest
    def preflight(self,rollback):
        target,manifest=self.verify_release(self.target);_,old=self.verify_release(rollback)
        if manifest['binary_sha256']!=old['binary_sha256']:raise MaintenanceError('binary_change_refused')
        for name in ['tools/ai_integration.py','tools/ai_adapters.py','tools/tui_gateway.py']:
            if name not in manifest['files']:raise MaintenanceError('modular_wrapper_missing')
        definition=plistlib.loads(self.plist.read_bytes())
        if definition.get('Label')!=LABEL or definition.get('ProgramArguments')!=[str(self.launcher),'network','serve']:
            raise MaintenanceError('unexpected_service_definition')
        configured=definition.get('EnvironmentVariables',{}).get('HARMONIKA_CONFIG_DIR',str(self.config))
        if Path(configured).resolve()!=self.config.resolve():raise MaintenanceError('unexpected_service_config')
        # Import only after signed runtime files have been verified.
        sys.path.insert(0,str(target/'tools'))
        spec=importlib.util.spec_from_file_location('gateway_verified_runtime',target/'tools/tui_gateway.py')
        self.gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.gateway)
        self.key=self.gateway.read_key(self.config);self.port=self.gateway.settings(self.config)['port']
        self.assert_listener()
        return {'target':self.target,'rollback':rollback,'signature':'verified','binary_unchanged':True}
    def assert_listener(self):
        pid=self.pid()
        result=checked_run(['lsof','-nP','-iTCP:'+str(self.port),'-sTCP:LISTEN','-Fp'])
        owners={int(line[1:]) for line in result.stdout.splitlines() if re.fullmatch(r'p[0-9]+',line)}
        if owners!={pid}:raise MaintenanceError('gateway_listener_identity_mismatch')
    def wait_idle(self,timeout=40,quiet_seconds=3):
        until=time.monotonic()+timeout;idle_since=None
        while time.monotonic()<until:
            pid=self.pid()
            if not pid:
                # A stopped/crashed service has no owned requests to drain.
                listener=checked_run(['lsof','-nP','-iTCP:'+str(self.port),'-sTCP:LISTEN','-Fp'])
                if listener.stdout.strip():raise MaintenanceError('port_owned_by_other_process')
                return
            self.assert_listener()
            result=checked_run(['lsof','-nP','-a','-p',str(pid),'-iTCP','-F','nT'])
            if result.returncode not in (0,1):raise MaintenanceError('connection_inspection_failed')
            if connection_states(result.stdout):idle_since=None
            else:
                idle_since=idle_since or time.monotonic()
                if time.monotonic()-idle_since>=quiet_seconds:return
            time.sleep(.25)
        raise MaintenanceError('gateway_busy_restart_refused')
    def switch(self,release):self.runtime.switch(release,self.root)
    def restart(self):
        # User explicitly requested restarting this exact LaunchAgent. No PID kill,
        # process matching, global unload, native session, or unrelated service.
        result=checked_run(['launchctl','kickstart','-k',self.service],timeout=20)
        if result.returncode:raise MaintenanceError('gateway_restart_failed')
    def request(self,path,body=None,authenticated=True,timeout=25):
        headers={'Content-Type':'application/json'}
        if authenticated:headers['Authorization']='Bearer '+self.key
        request=urllib.request.Request('http://127.0.0.1:'+str(self.port)+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        return self.gateway.opener().open(request,timeout=timeout)
    def health(self,contract):
        with self.request('/health',timeout=2) as response:value=json.load(response)
        return value.get('ok') is True and (contract is None or value.get('integration_contract')==contract)
    def wait_health(self,contract=1,timeout=25):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                if self.health(contract):self.assert_listener();return
            except (OSError,ValueError,urllib.error.URLError):pass
            time.sleep(.25)
        raise MaintenanceError('gateway_health_failed')
    def verify(self):
        try:
            with self.request('/health',authenticated=False,timeout=2):pass
            raise MaintenanceError('authentication_not_enforced')
        except urllib.error.HTTPError as error:
            if error.code!=401:raise MaintenanceError('unexpected_auth_response') from None
        with self.request('/v1/models',timeout=3) as response:models=json.load(response)
        if not any(row.get('id')=='harmonika-ai' for row in models.get('data',[])):raise MaintenanceError('model_alias_missing')
        marker='HARMONIKA_GATEWAY_OK';answer=[];terminals=0;done=0;wire=0;start=time.monotonic()
        decoder=self.gateway.integration.protocol.SSEDecoder()
        payload={'model':'harmonika-ai','messages':[{'role':'user','content':'Balas hanya dengan '+marker+'. Jangan gunakan tool.'}],'max_tokens':32,'stream':True}
        with self.request('/v1/chat/completions',payload) as response:
            if 'text/event-stream' not in response.headers.get('Content-Type',''):raise MaintenanceError('sse_content_type_invalid')
            while True:
                if time.monotonic()-start>45:raise MaintenanceError('sse_smoke_timeout')
                chunk=response.read1(16384);wire+=len(chunk)
                if wire>1024*1024:raise MaintenanceError('sse_smoke_too_large')
                if not chunk:break
                for event in decoder.feed(chunk):
                    if event['data']=='[DONE]':done+=1;continue
                    if done:raise MaintenanceError('event_after_done')
                    value=json.loads(event['data'])
                    if value.get('error'):raise MaintenanceError('sse_backend_error')
                    for choice in value.get('choices',[]):
                        if choice.get('finish_reason') is not None:terminals+=1
                        text=choice.get('delta',{}).get('content')
                        if text:answer.append(text)
        text=''.join(answer)
        if decoder.finish()['incomplete_event'] or done!=1 or terminals!=1 or text.count(marker)!=1:raise MaintenanceError('sse_contract_failed')
        self.last_checks={'health_contract':1,'unauthenticated_http':401,'models':'ok','sse':'passed','terminal_events':terminals,'done_events':done,'sse_seconds':round(time.monotonic()-start,2)}
        self.last_checks['native']=self.verify_native()
        doctor=checked_run([str(self.launcher),'doctor'],timeout=25)
        if doctor.returncode or json.loads(doctor.stdout).get('ok') is not True:raise MaintenanceError('doctor_failed')
        self.last_checks['doctor']='ok'
        return self.last_checks
    def verify_native(self):
        marker='HARMONIKA_NATIVE_OK';start=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='harmonika-gateway-activation-',dir='/tmp') as directory:
            root=Path(directory).resolve();cfg=root/'config';cfg.mkdir(mode=0o700)
            source=self.config/'harmonika.json';value=json.loads(source.read_text())
            endpoint='http://127.0.0.1:'+str(self.port)+'/v1'
            if value.get('providers',{}).get('harmonika',{}).get('base_url')!=endpoint:raise MaintenanceError('native_config_bypasses_gateway')
            value['mcp']={};value['lsp']={}
            for name in {'harmonika.json',source.resolve().name}:
                file=cfg/name;file.write_text(json.dumps(value));file.chmod(0o600)
            (root/'AGENTS.md').write_text('Isolated verification. No tools, files or network tasks. Answer the requested marker only.\n')
            snapshot=(self.root/'versions'/self.target/'tools/harmonika.py').read_text()
            env=native_environment(snapshot,cfg,os.environ)
            command=[str(self.launcher),'run','--quiet','--host','unix://'+str(root/'verify.sock'),'--data-dir',str(root/'data'),'--cwd',str(root),'Balas hanya dengan '+marker+'. Jangan gunakan tool.']
            # This child belongs only to this verification; existing writers untouched.
            child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True)
            try:stdout,_=child.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:child.communicate(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.communicate(timeout=5)
                raise MaintenanceError('native_smoke_timeout') from None
            if child.returncode or stdout.count(marker)!=1:raise MaintenanceError('native_smoke_failed')
        return {'passed':True,'seconds':round(time.monotonic()-start,2),'isolated_session':True,'tools_disabled':True}

class Maintenance:
    def __init__(self,system):self.system=system
    def apply(self,rollback):
        preflight=self.system.preflight(rollback)
        self.system.wait_idle()
        changed=False
        try:
            self.system.switch(self.system.target);changed=True;self.system.restart();self.system.wait_health(1)
            checks=self.system.verify()
            return {'ok':True,'action':'activated','release':self.system.target,'rollback_release':rollback,'preflight':preflight,'checks':checks}
        except Exception:
            if not changed:raise
            try:
                self.system.wait_idle();self.system.switch(rollback);self.system.restart();self.system.wait_health(None)
            except Exception:raise MaintenanceError('activation_failed_rollback_needs_attention') from None
            raise MaintenanceError('activation_failed_rolled_back') from None

@contextmanager
def maintenance_lock(root):
    file=root/'gateway-maintenance.lock'
    if file.is_symlink():raise MaintenanceError('unsafe_lock_path')
    with open(file,'a') as lock:
        os.chmod(file,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise MaintenanceError('maintenance_in_progress') from None
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

def main():
    parser=argparse.ArgumentParser(description='Harmonika Gateway scoped restart; dry-run by default.')
    parser.add_argument('--apply',action='store_true');parser.add_argument('--target-release');parser.add_argument('--rollback-release');parser.add_argument('--report',type=Path)
    args=parser.parse_args();system=System(args.target_release)
    try:
        with maintenance_lock(system.root),system.runtime.installation_lock(system.root):
            rollback=args.rollback_release or system.running_release()
            if args.apply:report=Maintenance(system).apply(rollback)
            else:report={'ok':True,'action':'preflight_only','preflight':system.preflight(rollback),'current_running_release':system.running_release()}
    except Exception as error:
        report={'ok':False,'error':str(error) if isinstance(error,MaintenanceError) else 'gateway_maintenance_failed','service':LABEL}
    if args.report:
        # Only the fixed structured report: no keys, replies, command output or history.
        args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return 0 if report['ok'] else 2
if __name__=='__main__':sys.exit(main())
