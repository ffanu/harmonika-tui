"""Official-source retrieval. A successful fetch is provenance, NOT current-law certification.

P5 source identity: quotable full text is stored ONLY when the actual fetched
document itself opens with the expected formal header ("UNDANG-UNDANG ...
NOMOR <number> TAHUN <year> TENTANG <expected subject>") position-bound near
the start of the extracted text. A mention of the expected law anywhere else
(cross references, amendment lists, navigation) never establishes identity.
HTML pages without recognized statute structure are official landing/metadata
pages: they are stored metadata-only (empty body, excluded from search and
citations) so they can never supply legal quotes; refresh degrades instead of
inventing statute text.
"""
import datetime as dt,hashlib,json,re,sqlite3,subprocess,sys,threading,time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from .common import require,DomainError,now_iso,parse_time
from .official_network import SafeFetcher
MANIFEST={
 'uu_1_2023':{'title':'UU 1/2023 — Kitab Undang-Undang Hukum Pidana','number':1,'year':2023,'url':'https://jdih.bmkg.go.id/storage/common/dokumen/salinanuunomor1tahun2023.pdf','kind':'uu','subjects':('kitab','pidana')},
 'uu_1_2024':{'title':'UU 1/2024 — Perubahan Kedua UU ITE','number':1,'year':2024,'url':'https://jdih.komdigi.go.id/produk_hukum/pratinjau/id/884','kind':'uu','subjects':('informasi','transaksi','elektronik')},
 'uu_1_2026':{'title':'UU 1/2026 — Penyesuaian Pidana','number':1,'year':2026,'url':'https://peraturan.bpk.go.id/Download/400929/UU%20Nomor%201%20Tahun%202026.pdf','kind':'uu','subjects':('pidana',)},
 'uu_20_2025':{'title':'UU 20/2025 — Kitab Undang-Undang Hukum Acara Pidana','number':20,'year':2025,'url':'https://peraturan.go.id/files/uu-no-20-tahun-2025.pdf','kind':'uu','subjects':('kitab','acara','pidana')},
 'uu_27_2022':{'title':'UU 27/2022 — Pelindungan Data Pribadi','number':27,'year':2022,'url':'https://jdih.komdigi.go.id/produk_hukum/pratinjau/id/832','kind':'uu','subjects':('pelindungan','data','pribadi')},
}
HOSTS=frozenset(('peraturan.go.id','www.peraturan.go.id','jdih.komdigi.go.id','jdih.bmkg.go.id','peraturan.bpk.go.id'))
HEADER_WINDOW=4000
SUBJECT_WINDOW=400
PAGE_IDENTITY_WINDOW=3000
PAGE_SUBJECT_WINDOW=600
MIN_PASAL_HEADINGS=3
MIN_ARTICLE_PROSE=40
TTL_SECONDS=86400
HEADER=re.compile(r'undang\s*[-.]\s*undang(?:\s+republik)?(?:\s+indonesi\.?a)?\s+nomor\s*(\d{1,4})(?!\d)\s+tahun\s*(\d{4})(?!\d)',re.I)
PAGE_IDENTITY=re.compile(r'nomor\s*:?\s*(\d{1,4})(?!\d)\D{0,40}?tahun\s*:?\s*(\d{4})(?!\d)',re.I)
PASAL_HEADING=re.compile(r'(?im)^[ \t]*pasal[ \t]+(\d{1,4}|[ivxlcdm]{1,6})[ \t]*$')
META_KEYS=frozenset(('title','description','og:title','og:description','twitter:title','twitter:description'))
class OfficialFetcher(SafeFetcher):
 def _open_connection(self,address,port,hostname,https,timeout):
  require(hostname in HOSTS and https,'official_source_required',403)
  return super()._open_connection(address,port,hostname,https,timeout)
class Text(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.skip=0
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style'):self.skip+=1
  if tag=='meta':
   d=dict(attrs);key=(d.get('name') or d.get('property') or '').lower()
   if key in META_KEYS and d.get('content'):self.parts.append(d['content']+'\n')
  if tag in ('p','div','br','tr','h1','h2','h3','h4','h5','h6','li','td','th','table','ul','ol','section','article','option','title'):self.parts.append('\n')
 def handle_endtag(self,tag):
  if tag in ('script','style'):self.skip=max(0,self.skip-1)
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)
def _letters(text):return re.sub(r'[^a-z]','',text)
def _header_start(text,item):
 """Expected number/year/subject matched on the document's own opening header.

 Returns the offset where the actual document main content starts, or None
 when the header is absent, mismatched, beyond the opening window, or lacks
 the expected subject. OCR noise is tolerated by comparing letter-only text.
 """
 low=text.lower();m=HEADER.search(low[:HEADER_WINDOW*2])
 if m is None or int(m.group(1))!=item['number'] or int(m.group(2))!=item['year']:return None
 if not all(s in _letters(low[m.end():m.end()+SUBJECT_WINDOW*2]) for s in item['subjects']):return None
 return low.rfind('\n',0,m.start())+1
def _page_identity(text,item):
 """Title-region identity for metadata-only storage; never grants quotable text."""
 compact=re.sub(r'\s+',' ',text.lower());t=PAGE_IDENTITY.search(compact[:PAGE_IDENTITY_WINDOW])
 if t is None or int(t.group(1))!=item['number'] or int(t.group(2))!=item['year']:return False
 return all(s in _letters(compact[t.start():t.start()+PAGE_SUBJECT_WINDOW]) for s in item['subjects'])
def _recognized_document(text):
 """True only for recognized statute structure: the opening Pasal 1..3 (or I..III)
 headings must each be followed by real article prose before the next heading.
 Table-of-contents/preview widgets list headings back-to-back with no prose."""
 matches=list(PASAL_HEADING.finditer(text))
 if len(matches)<MIN_PASAL_HEADINGS:return False
 wanted=('1','i','2','ii','3','iii');found=0;i=0
 while i<len(matches) and found<MIN_PASAL_HEADINGS:
  if matches[i].group(1).lower()==wanted[found*2] or matches[i].group(1).lower()==wanted[found*2+1]:
   end=matches[i+1].start() if i+1<len(matches) else len(text)
   if len(_letters(text[matches[i].end():end]))<MIN_ARTICLE_PROSE:return False
   found+=1
  i+=1
 return found>=MIN_PASAL_HEADINGS
class Sources:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);self.lock=threading.Lock();self.fetch_lock=threading.Lock();self.attempts={}
  with sqlite3.connect(self.path) as d:d.execute('CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,base_id TEXT,sha256 TEXT,fetched_at TEXT,data TEXT)')
  self.path.chmod(0o600)
 def refresh(self,base_id):
  require(base_id in MANIFEST,'source_not_allowlisted',403)
  with self.lock:
   now=time.monotonic();last=self.attempts.get(base_id);require(last is None or now-last>=30,'source_refresh_throttled',429);self.attempts[base_id]=now
  item=MANIFEST[base_id]
  require(self.fetch_lock.acquire(False),'source_capacity',429)
  try:
   v=OfficialFetcher(max_bytes=24*1024*1024,budget_seconds=18,timeout_seconds=8).fetch(item['url']);require(v['status']==200 and urlsplit(v['url']).hostname in HOSTS,'source_unavailable',503)
   raw=v['body_bytes'];pdf=raw.startswith(b'%PDF-')
   if pdf:
    child=subprocess.run([sys.executable,'-I',str(Path(__file__).with_name('pdf_source.py'))],input=raw,capture_output=True,timeout=14);require(child.returncode==0 and len(child.stdout)<=3000000,'source_parse_failed',503);text=json.loads(child.stdout)['text']
   else:
    require(v['content_type'] in ('text/html','application/xhtml+xml'),'unsupported_source_type',503);parser=Text();parser.feed(v['text']);text=''.join(parser.parts)
   text=re.sub('[ \t\r\f]+',' ',text);start=_header_start(text,item)
   if pdf:
    require(start is not None,'source_identity_mismatch',503)
    header=HEADER.search(text.lower()[:HEADER_WINDOW*2]);prefix=_letters(text[:header.start()].lower())
    logo_words=('salinan','menimbang','mengingat','presiden','fresiden','presden','republik','repubuk','repueuk','repuelik','repijbuk','repubik','indonesia','indoneia')
    require(not re.sub('|'.join(sorted(logo_words,key=len,reverse=True)),'',prefix),'source_identity_mismatch',503)
    body=text[start:];form='pdf_full_text';verified=True
   elif start is not None and _recognized_document(text[start:]):
    body=text[start:];form='html_full_text';verified=True
   else:
    require(not pdf and _page_identity(text,item),'source_identity_mismatch',503)
    body='';form='metadata_only';verified=False
   if verified:require(100<=len(body)<=900000,'source_parse_failed',503)
   sha=hashlib.sha256(body.encode()).hexdigest();id=base_id+'_'+sha[:12]
   source={'id':id,'base_id':base_id,'title':item['title'],'url':v['url'],'authority':urlsplit(v['url']).hostname,'jurisdiction':'ID','document_kind':item['kind'],'number':item['number'],'year':item['year'],'body':body,'fetched_at':now_iso(),'sha256':sha,'raw_sha256':hashlib.sha256(raw).hexdigest(),'provenance':'official_live_fetch','currentness':'not_certified','verification':{'official_host':True,'fetched_by_server':True,'host_allowlisted':True,'document_identity_verified':verified,'identity_basis':'document_header_number_year_subject' if verified else 'page_metadata_only'},'identity':{'verified_from_document_header':verified,'method':'official_document_header' if verified else 'official_page_metadata_only','expected_number':item['number'],'expected_year':item['year'],'expected_subject_terms':list(item['subjects']),'note':None if verified else 'landing_or_metadata_page: no statute structure, text unavailable and never quotable'},'document_form':form,'text_availability':'full_document_text' if verified else 'metadata_only_no_document_text','has_full_text':verified,'citation_allowed':verified,'ttl_seconds':TTL_SECONDS,'scope':'bounded_official_corpus_not_all_laws'}
   with sqlite3.connect(self.path) as d:
    count=d.execute('SELECT count(*) FROM sources').fetchone()[0];require(count<100 or d.execute('SELECT 1 FROM sources WHERE id=?',(id,)).fetchone(),'source_storage_capacity',429);d.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)',(id,base_id,sha,source['fetched_at'],json.dumps(source,ensure_ascii=False)))
   return {k:v for k,v in source.items() if k!='body'}
  except DomainError:raise
  except Exception:raise DomainError('source_unavailable',503) from None
  finally:self.fetch_lock.release()
 def get(self,id):
  require(type(id) is str and len(id)<=100,'invalid_source_id')
  with sqlite3.connect(self.path) as d:r=d.execute('SELECT data FROM sources WHERE id=? OR base_id=? ORDER BY fetched_at DESC LIMIT 1',(id,id)).fetchone()
  require(r is not None,'source_not_found',404);source=json.loads(r[0]);age=(parse_time(now_iso())-parse_time(source['fetched_at'])).total_seconds();require(0<=age<=TTL_SECONDS,'source_refresh_required',409)
  if source.get('has_full_text'):require(source.get('verification',{}).get('document_identity_verified') is True,'source_refresh_required',409)
  return source
 def search(self,query,jurisdiction='ID',limit=10,kind=None):
  require(type(query) is str and 1<=len(query)<=500 and type(limit) is int and 1<=limit<=20,'invalid_source_query')
  if jurisdiction!='ID':return []
  with sqlite3.connect(self.path) as d:rows=d.execute('SELECT q.data FROM sources q WHERE q.id=(SELECT x.id FROM sources x WHERE x.base_id=q.base_id ORDER BY x.fetched_at DESC,x.id DESC LIMIT 1) ORDER BY q.fetched_at DESC LIMIT 10').fetchall()
  words=set(re.findall(r'\w{2,40}',query.lower())[:16]);found=[];seen=set()
  for r in rows:
   s=json.loads(r[0])
   if s['base_id'] in seen:continue
   seen.add(s['base_id']);age=(parse_time(now_iso())-parse_time(s['fetched_at'])).total_seconds()
   if not 0<=age<=TTL_SECONDS or kind and s['document_kind']!=kind:continue
   if not s.get('has_full_text') or not s.get('body') or s.get('verification',{}).get('document_identity_verified') is not True:continue
   hay=(s['title']+' '+s['body']).lower();score=sum(min(hay.count(w),20) for w in words)
   if score:found.append((score,s))
  found.sort(key=lambda x:(-x[0],x[1]['id']));return [s for _,s in found[:limit]]
 def history(self,id):
  require(id in MANIFEST,'source_not_allowlisted',403)
  with sqlite3.connect(self.path) as d:rows=d.execute('SELECT data FROM sources WHERE base_id=? ORDER BY fetched_at DESC LIMIT 100',(id,)).fetchall()
  return [{k:v for k,v in json.loads(r[0]).items() if k!='body'} for r in rows]

def citation(source,quote):
 require(type(quote) is str and 1<=len(quote)<=500 and quote in source.get('body',''),'unverified_citation',409)
 ver=source.get('verification') if isinstance(source.get('verification'),dict) else {}
 require(source.get('provenance')=='official_live_fetch' and ver.get('fetched_by_server') is True and source.get('has_full_text') is True and ver.get('document_identity_verified') is True,'unverified_source',409)
 return {'source_id':source['id'],'url':source['url'],'title':source['title'],'retrieved_at':source['fetched_at'],'sha256':source['sha256'],'quote':quote}
