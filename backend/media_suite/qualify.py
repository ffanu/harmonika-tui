"""Explicit synthetic/noncustomer fixtures only. Writes readiness after real validation."""
import argparse,base64,hashlib,io,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from process import execute
from contracts import require
MAP={'audio.transcribe':'asr','image.upscale':'upscale','audio.synthesize':'tts','browser.inspect':'browser'}
def qualify(op,fixture,expected=None):
 cfg=json.loads((ROOT/'config.json').read_text());request=json.loads(Path(fixture).read_text());assert request['synthetic_fixture'] is True
 rows=json.loads((ROOT/'model-receipt.json').read_text())
 for row in rows:
  p=ROOT/'models'/row['group']/Path(row['path']).name
  assert p.is_file() and not p.is_symlink() and p.stat().st_size==row['bytes']
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
  assert h.hexdigest()==row['sha256']
 py=ROOT/('.'+MAP[op])/('Scripts/python.exe' if os.name=='nt' else 'bin/python');start=time.monotonic()
 result=execute(py,ROOT/'engines.py',{'skill_id':op,'input':request['input']},360,ROOT)
 require(result['status']=='completed','real_test_failed');r=result['result']
 if op=='audio.transcribe':require(bool(expected) and expected.casefold() in r['transcript'].casefold(),'transcript_mismatch')
 elif op=='image.upscale':
  from PIL import Image
  raw=base64.b64decode(r['image_base64'],validate=True)
  with Image.open(io.BytesIO(raw)) as im:im.verify()
  (ROOT/'synthetic-upscale.png').write_bytes(raw)
 elif op=='audio.synthesize':
  import wave
  raw=base64.b64decode(r['audio_base64'],validate=True)
  with wave.open(io.BytesIO(raw)) as w:require(w.getnframes()>0 and w.getframerate()>0,'invalid_audio_output')
  (ROOT/'synthetic-tts.wav').write_bytes(raw)
 else:require(any('Example Domain' in x['title'] for x in r['pages']),'browser_inspection_failed')
 state=json.loads((ROOT/'qualification.json').read_text());state[op]={'real_test_passed':True,'timestamp':time.time(),'latency_seconds':round(time.monotonic()-start,3),'research_only':op=='audio.synthesize'}
 tmp=ROOT/'qualification.tmp';tmp.write_text(json.dumps(state,indent=2));os.replace(tmp,ROOT/'qualification.json');return state[op]
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('skill',choices=list(MAP));p.add_argument('fixture');p.add_argument('--expected-transcript');a=p.parse_args();print(json.dumps(qualify(a.skill,a.fixture,a.expected_transcript)))
