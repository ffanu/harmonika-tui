#!/usr/bin/env python3
"""3072 real synthetic plugins, lazy execution, API pages and bounded event loop."""
import asyncio,hashlib,json,resource,shutil,sys,tempfile,time
from pathlib import Path
from aiohttp.test_utils import TestClient,TestServer
from engine_registry.discovery import Discovery
from master_engine.service import make_app
S=Path('/opt/harmonika-ai/discovery-scale-stage')
async def main():
 source=(S/'engine_registry/skills/text-metrics/skill.py').read_text();report={};count=3072
 with tempfile.TemporaryDirectory(prefix='harmonika-scale-bench-') as temp:
  base=Path(temp).resolve();root=base/'plugins';root.mkdir()
  for i in range(count):
   p=root/('mod-%04d'%i);p.mkdir();text=source.replace('text.metrics','fixture.large_%04d'%i);(p/'skill.py').write_text(text);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(text.encode()).hexdigest()}))
  engine=Discovery(root,base/'state.db');gaps=[];stop=False
  async def heartbeat():
   previous=time.monotonic()
   while not stop:
    await asyncio.sleep(.02);now=time.monotonic();gaps.append(now-previous);previous=now
  beat=asyncio.create_task(heartbeat());before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;start=time.monotonic();cold=await engine.refresh();cold_seconds=time.monotonic()-start
  assert cold['registered']==count and cold['metadata_subprocess']==0,'cold_registry_incomplete'
  assert all(not isinstance(x.source,bytes) for x in engine._snapshot.values()),'eager_source_retention'
  start=time.monotonic();warm=await engine.refresh();warm_seconds=time.monotonic()-start;assert warm['metadata_cached']==count
  client=TestClient(TestServer(make_app('x'*64,skills={},dispatcher=lambda *a:None,mobile_enabled=False,discovery=engine,discovery_poll_seconds=5)));await client.start_server();headers={'X-Harmonika-Master-Secret':'x'*64,'X-Harmonika-Principal':'api-token:900000000000777090'}
  try:
   r=await client.get('/skills?engine=discovery&limit=200',headers=headers);page=await r.json();assert r.status==200 and page['total']==count and len(page['skills'])==200
   r=await client.post('/invoke',headers=headers,json={'skill_id':'fixture.large_1279','input':{'text':'Harmonika scale test'}});v=await r.json();assert r.status==200 and v['result']['words']==3
   start=time.monotonic();pending=asyncio.create_task(engine.refresh());await asyncio.sleep(.001)
   r=await client.post('/invoke',headers=headers,json={'skill_id':'fixture.large_2000','input':{'text':'Wait for new snapshot'}});v=await r.json();assert r.status==200 and v['result']['words']==4;await pending;wait_seconds=time.monotonic()-start
  finally:await client.close()
  stop=True;await beat;assert max(gaps)<.5,'event_loop_blocked'
  peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;factor=1 if sys.platform=='darwin' else 1024
  increase=(peak-before)*factor/1048576;assert increase<256,'memory_growth_limit'
  report={'status':'passed','modules':count,'cold_seconds':round(cold_seconds,3),'warm_seconds':round(warm_seconds,3),'invoke_during_scan_seconds':round(wait_seconds,3),'max_event_loop_gap_seconds':round(max(gaps),3),'peak_rss_mib':round(peak*factor/1048576,2),'incremental_peak_rss_mib':round(increase,2),'source_bytes':cold['source_bytes'],'snapshot_metadata_bytes':cold['snapshot_metadata_bytes'],'cold_metadata_subprocesses':cold['metadata_subprocess'],'source_storage':'lazy_private_disk','page_and_real_module_execution':'passed','temporary_state_removed':True}
 (S/'benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
asyncio.run(main())
