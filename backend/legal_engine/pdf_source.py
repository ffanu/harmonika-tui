"""Isolated bounded public-law PDF extractor (not a forensic/evidence processor)."""
import io,json,sys
if sys.platform=='linux':
 import resource
 resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,384*1024*1024));resource.setrlimit(resource.RLIMIT_CPU,(10,10))
try:
 from pypdf import PdfReader
 raw=sys.stdin.buffer.read(24*1024*1024+1)
 if len(raw)>24*1024*1024:raise ValueError()
 reader=PdfReader(io.BytesIO(raw),strict=False)
 if reader.is_encrypted or len(reader.pages)>350:raise ValueError()
 parts=[];size=0
 for page in reader.pages:
  text=page.extract_text() or '';size+=len(text)
  if size>900000:raise ValueError()
  parts.append(text)
 print(json.dumps({'text':'\n'.join(parts),'pages':len(parts)}))
except Exception:sys.exit(2)
