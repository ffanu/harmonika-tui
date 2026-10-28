"""Async internal service boundary. Owner-only; never expose through public tokens."""
import asyncio,json,sys
from pathlib import Path

async def execute(skill_id,arguments,python=None,timeout=45):
    """One isolated connector worker per call, including the VNC reactor lifecycle."""
    if skill_id not in ('ssh.connect','ssh.exec','ssh.upload','ssh.download','ssh.tunnel','remote.targets','telnet.connect','telnet.exec','vnc.connect','vnc.capture','vnc.control'):
        return {'ok':False,'error':{'code':'unsupported_skill'}}
    worker=Path(__file__).with_name('connector.py')
    if skill_id.startswith('ssh.'):
        worker=Path(__file__).with_name('ssh_connector.py')
        if not worker.is_file():worker=Path(__file__).resolve().parents[2]/'runtime/tools/ssh_connector.py'
    payload=json.dumps({'skill_id':skill_id,'input':arguments}).encode()
    if len(payload)>32768:return {'ok':False,'error':{'code':'input_too_large'}}
    process=await asyncio.create_subprocess_exec(python or sys.executable,'-I',str(worker),*(['invoke'] if skill_id.startswith('ssh.') else []),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=131072)
    try:
        output,_=await asyncio.wait_for(process.communicate(payload),min(180,max(1,timeout)))
        if len(output)>131072:return {'ok':False,'error':{'code':'remote_output_limit'}}
        result=json.loads(output)
        if not isinstance(result,dict) or type(result.get('ok')) is not bool:raise ValueError()
        return result
    except (asyncio.TimeoutError,asyncio.CancelledError):
        # This is exclusively the worker spawned by this invocation.
        if process.returncode is None:process.kill()
        await process.wait()
        return {'ok':False,'status':'uncertain' if skill_id in ('ssh.exec','ssh.upload','telnet.exec','vnc.control') else 'error','error':{'code':'remote_timeout','retry_safe':False}}
    except (ValueError,UnicodeError):return {'ok':False,'error':{'code':'remote_invalid_response'}}
