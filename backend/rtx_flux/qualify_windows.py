"""Real local-GPU quality/latency fixtures; no external inference or customer data."""
import base64,hashlib,io,json,os,subprocess,threading,time,urllib.request
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parent
CASES=[
 ('indonesia-future','A cinematic wide view of Indonesia in 2045, clearly visible Monas monument in a green Jakarta city center, contemporary buildings inspired by traditional Indonesian rumah gadang curved roofs, a red and white Indonesian flag in the foreground, a sleek elevated electric train crossing the scene, tropical gardens and pedestrians, golden morning light, photorealistic architectural visualization, coherent city composition, wide establishing shot, no text or watermark.'),
 ('router-product','Professional studio product photograph of a white WiFi router with exactly four black antennas on a clean pale blue desk, a soft blue light glows under the router, minimal modern home office background, sharp product detail, physically plausible reflections, premium commercial lighting, no text, no watermark.'),
 ('typography','A minimalist premium advertising poster. The large headline reads exactly HARMONIKA. Beneath it, smaller text reads INTERNET CEPAT. White background, clean blue bold sans serif typography, a small photorealistic white WiFi router centered below the words, balanced layout, no other words.'),
 ('technician','Documentary photograph of an Indonesian female network technician wearing a blue work shirt, gently holding a small fiber optic cable connector between the fingers of her right hand, both hands visible and anatomically natural, focused calm expression, soft natural daylight, realistic skin texture and professional home installation setting, no text.'),
 ('interior','Photorealistic interior design visualization of a modern Indonesian living room, teak wood sofa with cream cushions, large tropical garden windows, woven rattan pendant lamp, elegant white WiFi router on a side table, a small batik accent pillow, warm late afternoon light, wide angle, correct perspective and coherent furniture, no text.')]
def post(data):
 req=urllib.request.Request('http://127.0.0.1:18191/generate',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Harmonika-Qualification':'local-test'})
 start=time.monotonic()
 with urllib.request.urlopen(req,timeout=180) as r:
  first=r.read(1);first_ms=round((time.monotonic()-start)*1000);v=json.loads(first+r.read())
 return v,first_ms,time.monotonic()-start
def main():
 if os.name!='nt':raise RuntimeError('windows_gpu_qualification_only')
 stop=threading.Event();samples=[]
 def monitor():
  while not stop.is_set():
   try:
    r=subprocess.run(['nvidia-smi','--query-gpu=memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
    if r.returncode==0:
     used,total,util=map(int,r.stdout.strip().splitlines()[0].split(','));samples.append({'used_mib':used,'total_mib':total,'util_percent':util})
   except Exception:pass
   stop.wait(1)
 thread=threading.Thread(target=monitor,daemon=True);thread.start()
 out=ROOT/'qualification';out.mkdir(exist_ok=True);rows=[]
 for i,(name,prompt) in enumerate(CASES):
  v,first,elapsed=post({'prompt':prompt,'width':1024,'height':1024,'seed':20261025+i})
  if v.get('ok') is not True:raise RuntimeError('qualification_render_failed_'+name)
  binary=base64.b64decode(v['b64_png'],validate=True)
  with Image.open(io.BytesIO(binary)) as im:
   if im.size!=(1024,1024) or im.format!='PNG':raise RuntimeError('qualification_artifact_invalid')
   im.verify()
  if first>12000:raise RuntimeError('qualification_first_byte_too_late')
  (out/(name+'.png')).write_bytes(binary)
  rows.append({'name':name,'seconds':round(elapsed,2),'first_byte_ms':first,'bytes':len(binary),'sha256':hashlib.sha256(binary).hexdigest(),'seed':v.get('seed')})
  (ROOT/'qualification-progress.json').write_text(json.dumps({'status':'running','cases':rows},indent=2))
 stop.set();thread.join(timeout=4)
 report={'peak_total_gpu_used_mib':max((x['used_mib'] for x in samples),default=None),'gpu_samples':len(samples),'status':'technical_passed_visual_review_required','model':'black-forest-labs/FLUX.2-klein-4B','cases':rows,'local_gpu_only':True,'approved':False}
 (ROOT/'qualification-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':main()
