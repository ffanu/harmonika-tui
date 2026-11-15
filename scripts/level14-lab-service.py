"""Dedicated exam host only; no production service registration or host commands."""
import asyncio,json,os,sys
from pathlib import Path
from aiohttp import web,ClientSession,ClientTimeout
ROOT=Path('/opt/harmonika-exams/hl14-native')
sys.path.insert(0,str(ROOT/'source'));sys.path.insert(0,str(ROOT/'source/runtime/tools'))
from backend.coding_runtime.core import Runtime
from backend.coding_runtime.files import FileStore
from backend.coding_runtime.sandbox import DockerSandbox
from backend.coding_runtime.service import make_app
from engineering_evidence import redact
async def main():
 cfg=json.loads((ROOT/'private-config.json').read_text());session=ClientSession(timeout=ClientTimeout(total=160));shutdown=asyncio.Event()
 async def decide(payload):
  async with session.post('http://127.0.0.1:18189/v1/chat/completions',json=payload,headers={'X-Level14-Relay':cfg['secret']}) as res:
   value=await res.json()
   if res.status!=200:raise RuntimeError('lab_planner_unavailable')
   return value['choices'][0]['message']
 runtime=Runtime(ROOT/'state',DockerSandbox(image='harmonika-coding:20261024',phase2_runtime=False),FileStore,redact,phase2_enabled=False)
 app=make_app(runtime,cfg['secret'],decide)
 async def close(req):shutdown.set();return web.json_response({'shutdown':'requested'})
 app.router.add_post('/__exam_shutdown',close)
 runner=web.AppRunner(app,access_log=None);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',18186);await site.start();(ROOT/'service-state.json').write_text(json.dumps({'pid':os.getpid(),'port':18186,'started':True}))
 try:await shutdown.wait()
 finally:await runner.cleanup();await session.close()
asyncio.run(main())
