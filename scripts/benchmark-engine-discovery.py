#!/usr/bin/env python3
"""Synthetic 256-module benchmark; separate temp paths, no model calls."""
import asyncio,hashlib,json,shutil,tempfile,time,sys
from pathlib import Path
from engine_registry.discovery import Discovery
async def main():
 source=Path('/opt/harmonika-ai/discovery-stage/engine_registry/skills/text-metrics/skill.py').read_text()
 with tempfile.TemporaryDirectory(prefix='harmonika-discovery-bench-') as temp:
  base=Path(temp).resolve();root=base/'plugins';root.mkdir()
  for i in range(256):
   p=root/('module-%03d'%i);p.mkdir();text=source.replace('text.metrics','fixture.module_%03d'%i);(p/'skill.py').write_text(text);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(text.encode()).hexdigest()}))
  engine=Discovery(root,base/'status.db');gaps=[];stop=False
  async def heartbeat():
   previous=time.monotonic()
   while not stop:
    await asyncio.sleep(.02);now=time.monotonic();gaps.append(now-previous);previous=now
  beat=asyncio.create_task(heartbeat());started=time.monotonic();first=await engine.refresh();cold=time.monotonic()-started
  assert first['registered']==256 and first['rejected']==0,'incomplete_benchmark_registry'
  started=time.monotonic();second=await engine.refresh();warm=time.monotonic()-started
  result=await engine.execute('fixture.module_127',{'text':'Harmonika engine test'});assert result['words']==3
  stop=True;await beat;assert max(gaps)<.5,'event_loop_blocked'
  report={'status':'passed','modules':256,'cold_seconds':round(cold,3),'warm_seconds':round(warm,3),'max_event_loop_gap_seconds':round(max(gaps),3),'sample_execute':'passed','temporary_state_removed':True}
  Path('/opt/harmonika-ai/discovery-stage/benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
asyncio.run(main())
