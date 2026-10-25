"""Fixed PDF decoder subprocess: no case key, URL, filesystem path or dynamic code input."""
import base64,io,json,subprocess,sys,time
from pathlib import Path
import threading
PARSER_LOCK=threading.Lock()

def extract_pdf(data,pages_cap,deadline):
 if not PARSER_LOCK.acquire(False):return {'text':'','truncated':False,'deadline_exceeded':False,'pages_seen':0,'page_total':None,'parse_unavailable':True}
 try:return _extract_pdf(data,pages_cap,deadline)
 finally:PARSER_LOCK.release()

def _extract_pdf(data,pages_cap,deadline):
 timeout=max(.1,min(10,deadline-time.monotonic()))
 try:
  payload=json.dumps({'bytes':base64.b64encode(data).decode(),'pages':min(100,int(pages_cap))}).encode()
  r=subprocess.run([sys.executable,'-I',str(Path(__file__).resolve()),'worker'],input=payload,capture_output=True,timeout=timeout)
  if r.returncode or len(r.stdout)>1600000:raise ValueError()
  return json.loads(r.stdout)
 except subprocess.TimeoutExpired:return {'text':'','truncated':True,'deadline_exceeded':True,'pages_seen':0,'page_total':None,'parse_unavailable':True}
 except Exception:return {'text':'','truncated':False,'deadline_exceeded':False,'pages_seen':0,'page_total':None,'parse_unavailable':True}

def worker():
 if sys.platform=='linux':
  import resource
  resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,384*1024*1024));resource.setrlimit(resource.RLIMIT_CPU,(8,8))
 from pypdf import PdfReader
 v=json.loads(sys.stdin.buffer.read(15*1024*1024));raw=base64.b64decode(v['bytes'],validate=True)
 if len(raw)>10*1024*1024:raise ValueError()
 reader=PdfReader(io.BytesIO(raw),strict=False)
 if reader.is_encrypted:raise ValueError()
 total=len(reader.pages)
 if total>1000:raise ValueError()
 cap=min(100,max(1,int(v['pages'])));texts=[];size=0
 for page in reader.pages[:cap]:
  text=page.extract_text() or '';size+=len(text)
  if size>200000:raise ValueError()
  texts.append(text)
 print(json.dumps({'text':'\n'.join(texts),'truncated':total>cap,'deadline_exceeded':False,'pages_seen':min(total,cap),'page_total':total}))
if __name__=='__main__':
 try:worker()
 except Exception:sys.exit(2)
