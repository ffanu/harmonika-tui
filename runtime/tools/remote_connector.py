"""Owner-only remote access core: registered SSH, Telnet and VNC targets.
No passwords, endpoints or screenshots are persisted in audit records.
"""
import asyncio, importlib.util, io, json, logging, os, re, select, socket, threading, time
from contextlib import contextmanager
from pathlib import Path


def ssh_core():
    source=Path(__file__).with_name('ssh_connector.py')
    if not source.is_file():source=Path(__file__).resolve().parents[2]/'runtime/tools/ssh_connector.py'
    spec=importlib.util.spec_from_file_location('harmonika_ssh_core',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

ssh=ssh_core()
SKILLS=('remote.targets','telnet.connect','telnet.exec','vnc.connect','vnc.capture','vnc.control')

def targets(config, query=''):
    ssh.require(isinstance(query,str) and len(query)<=253,'invalid_input')
    found=[]
    for alias,t in config['targets'].items():
        endpoint=f"{t['username']}@{t['host']}:{t.get('port',22)}"
        if not query or query in (alias,t['host'],endpoint,'ssh://'+endpoint):
            found.append({'target':alias,'protocol':'ssh','endpoint':endpoint,'authentication':'public_key'})
    for alias,t in config.get('remote_targets',{}).items():
        if not query or query in (alias,t.get('via_ssh')):
            found.append({'target':alias,'protocol':t['protocol'],'via_ssh':t.get('via_ssh'),'ready':True})
    return {'targets':found,'requires_operator_registration':not bool(found)}

def remote_target(config, alias, protocol):
    ssh.require(isinstance(alias,str) and re.fullmatch('[A-Za-z0-9_-]{1,40}',alias),'invalid_input')
    t=config.get('remote_targets',{}).get(alias)
    ssh.require(isinstance(t,dict) and t.get('protocol')==protocol,'target_not_registered')
    ssh.require(type(t.get('port')) is int and 1<=t['port']<=65535,'invalid_config')
    ssh.require(t.get('host') in ('127.0.0.1','::1','localhost') or t.get('allow_private_destination') is True,'remote_destination_not_authorized')
    ssh.require(isinstance(t.get('host'),str) and re.fullmatch(r'[A-Za-z0-9.:-]{1,253}',t['host']),'invalid_config')
    ssh.target(config,t.get('via_ssh'))
    return t

@contextmanager
def channel(config,t):
    """Encrypted SSH direct-tcpip; no public Telnet/VNC listeners are opened."""
    with ssh.connection(ssh.target(config,t['via_ssh'])) as client:
        stream=client.get_transport().open_channel('direct-tcpip',(t['host'],t['port']),('127.0.0.1',0),timeout=8)
        try:stream.settimeout(8);yield stream
        finally:stream.close()

@contextmanager
def forwarded(config,t):
    """Single local client, ephemeral loopback port, bounded forward lifetime."""
    with channel(config,t) as upstream:
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.2)
        stopped=threading.Event();downstream=[]
        def pump():
            client=None
            try:
                deadline=time.monotonic()+10
                while not stopped.is_set() and time.monotonic()<deadline:
                    try:client,_=listener.accept();break
                    except socket.timeout:continue
                if client is None:return
                downstream.append(client);client.settimeout(2)
                while not stopped.is_set():
                    ready,_,_=select.select([client,upstream],[],[],.2)
                    for src in ready:
                        data=src.recv(65536)
                        if not data:return
                        (upstream if src is client else client).sendall(data)
            except (OSError,EOFError):pass
            finally:
                if client:client.close()
        worker=threading.Thread(target=pump,daemon=True);worker.start()
        try:yield listener.getsockname()[1]
        finally:
            stopped.set();listener.close()
            for peer in downstream:
                try:peer.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            upstream.close();worker.join(3)

def credential(t,name):
    p=t.get(name+'_file')
    if not p:return None
    value=ssh.private_file(p).read_text().rstrip('\r\n')
    ssh.require(0<len(value)<=1024 and '\n' not in value and '\r' not in value,'invalid_credential')
    return value

async def telnet(port,t,args,execute):
    import telnetlib3
    reader,writer=await asyncio.wait_for(telnetlib3.open_connection('127.0.0.1',port,connect_minwait=0,connect_maxwait=.1,limit=65536),8)
    prompt=t.get('prompt','# ')
    ssh.require(isinstance(prompt,str) and 1<=len(prompt)<=80,'invalid_config')
    # Prompt is an operator-owned literal, never a model-supplied regex.
    async def until(ending):
        data=''
        while not data.endswith(ending):
            chunk=await asyncio.wait_for(reader.read(256),8)
            ssh.require(bool(chunk),'remote_connection_closed');data+=chunk
            ssh.require(len(data.encode())<=65536,'remote_output_limit')
        return data
    try:
        user=credential(t,'username');password=credential(t,'password')
        if user:
            await until(t.get('username_prompt','login: '));writer.write(user+'\r\n');await writer.drain()
        if password:
            await until(t.get('password_prompt','Password: '));writer.write(password+'\r\n');await writer.drain()
        await until(prompt)
        if not execute:return {'authenticated':True,'protocol':'telnet','encrypted_transport':'ssh','persistent_session':False}
        command=args.get('command')
        ssh.require(type(command) is str and command in t.get('allowed_commands',[]),'remote_command_not_allowed')
        writer.write(command+'\r\n');await writer.drain();output=await until(prompt)
        for secret in (user,password):
            if secret:output=output.replace(secret,'[redacted]')
        return {'executed':True,'output':output,'completion':'prompt_received','exit_code':None}
    finally:writer.close()

def vnc(port,t,args,skill):
    from vncdotool import api
    from vncdotool.client import VNCDoToolFactory,VNCDoToolClient
    from PIL import Image
    # Refuse huge initial geometry before allocating a framebuffer.
    class BoundedClient(VNCDoToolClient):
        def vncConnectionMade(self):
            if self.width*self.height>16777216:raise ssh.SSHError('vnc_frame_too_large')
            return super().vncConnectionMade()
    class Factory(VNCDoToolFactory):protocol=BoundedClient
    old=Image.MAX_IMAGE_PIXELS;Image.MAX_IMAGE_PIXELS=16777216
    client=None
    try:
        client=api.connect('127.0.0.1::'+str(port),password=credential(t,'password'),username=credential(t,'username'),factory_class=Factory,timeout=10)
        frame=client.refreshScreen();width,height=frame.screen.size
        result={'authenticated':True,'protocol':'vnc','encrypted_transport':'ssh','width':width,'height':height,'persistent_session':False}
        if skill=='vnc.control':
            ssh.require(t.get('allow_control') is True,'vnc_control_not_authorized')
            action=args.get('action')
            if action=='click':
                x,y=args.get('x'),args.get('y');ssh.require(type(x) is int and type(y) is int and 0<=x<width and 0<=y<height,'invalid_input')
                client.mouseMove(x,y);client.mousePress(1)
            elif action=='key':
                key=args.get('key');ssh.require(key in ('enter','tab','esc','up','down','left','right','home','end','backspace','space','ctrl-c','ctrl-a'),'invalid_input');client.keyPress(key)
            elif action=='text':
                text=args.get('text');ssh.require(isinstance(text,str) and len(text)<=512 and all(32<=ord(c)<=126 for c in text),'invalid_input')
                client.type(text)
            else:raise ssh.SSHError('invalid_input')
            result['action_sent']=True
        if skill=='vnc.capture':
            ssh.require(t.get('allow_capture') is True,'vnc_capture_not_authorized')
            destination=ssh.local_path(ssh.load_config(),{**args,'remote_path':'unused'})
            ssh.require(destination.suffix.lower()=='.png','invalid_input')
            fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                with os.fdopen(fd,'wb') as file:frame.screen.save(file,format='PNG')
            except BaseException:
                destination.unlink();raise
            result['artifact']={'path':str(destination),'mime_type':'image/png','size_bytes':destination.stat().st_size}
        return result
    finally:
        if client:client.disconnect()
        api.shutdown()
        Image.MAX_IMAGE_PIXELS=old

FIELDS={'remote.targets':{'query'},'telnet.connect':{'target'},'telnet.exec':{'target','command'},'vnc.connect':{'target'},'vnc.capture':{'target','project_root','local_path'},'vnc.control':{'target','action','x','y','key','text'}}
def invoke(skill,args):
    started=time.monotonic();result={'schema_version':1,'skill_id':skill,'provider':'Harmonika Ai'}
    try:
        ssh.require(skill in SKILLS and type(args) is dict and not set(args)-FIELDS[skill],'invalid_input')
        config=ssh.load_config();ssh.audit(skill,'started','',0)
        if skill=='remote.targets':data=targets(config,args.get('query',''))
        else:
            protocol=skill.split('.')[0];t=remote_target(config,args.get('target'),protocol)
            if skill=='telnet.exec':ssh.require(args.get('command') in t.get('allowed_commands',[]),'remote_command_not_allowed')
            if skill=='vnc.control':ssh.require(t.get('allow_control') is True,'vnc_control_not_authorized')
            with forwarded(config,t) as port:
                if protocol=='telnet':data=asyncio.run(asyncio.wait_for(telnet(port,t,args,skill=='telnet.exec'),30))
                else:data=vnc(port,t,args,skill)
        result.update(ok=True,status='completed',data=data)
    except Exception as exc:
        code=exc.code if isinstance(exc,ssh.SSHError) else 'remote_connection_failed'
        if isinstance(exc,(TimeoutError,socket.timeout,asyncio.TimeoutError)):code='remote_timeout'
        uncertain=skill in ('telnet.exec','vnc.control') and code in ('remote_connection_failed','remote_timeout','remote_connection_closed')
        result.update(ok=False,status='uncertain' if uncertain else 'error',error={'code':code,'retry_safe':False})
    try:ssh.audit(skill if skill in SKILLS else 'remote.invalid',result['status'],result.get('error',{}).get('code',''),int((time.monotonic()-started)*1000))
    except Exception:result['audit_available']=False
    return result

def main():
    raw=__import__('sys').stdin.buffer.read(32769);ssh.require(len(raw)<=32768,'input_too_large');p=json.loads(raw)
    ssh.require(type(p) is dict and set(p)=={'skill_id','input'},'invalid_input')
    print(json.dumps(invoke(p['skill_id'],p['input']),ensure_ascii=False))
if __name__=='__main__':
    for name in ('paramiko','vncdotool','twisted'):logging.getLogger(name).addHandler(logging.NullHandler());logging.getLogger(name).propagate=False
    try:main()
    except Exception:print(json.dumps({'ok':False,'status':'error','error':{'code':'invalid_input'}}))
