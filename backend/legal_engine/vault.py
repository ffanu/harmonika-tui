"""Immutable encrypted originals. Names/claims do not establish forensic authenticity."""
import hashlib,io,os,re,zipfile
from pathlib import Path
from uuid import uuid4
from .common import require,DomainError,identifier,now_iso
MAX_FILE=10*1024*1024

def sniff(raw):
 require(type(raw) is bytes and 0<len(raw)<=MAX_FILE,'file_too_large',413)
 dims={}
 if raw.startswith((b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff')) or raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':
  try:
   from PIL import Image
   with Image.open(io.BytesIO(raw)) as img:
    require(img.format in ('PNG','JPEG','WEBP') and img.width*img.height<=40000000,'unsupported_image');dims={'width':img.width,'height':img.height};mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[img.format];img.verify()
  except DomainError:raise
  except Exception:raise DomainError('corrupt_image') from None
  return mime,dims
 if raw.startswith(b'%PDF-'):return 'application/pdf',{'container_validation':'header_only'}
 if raw.startswith(b'PK\x03\x04'):
  try:
   with zipfile.ZipFile(io.BytesIO(raw)) as z:
    info=z.infolist();require(len(info)<=1000 and sum(x.file_size for x in info)<=20*1024*1024,'unsafe_document');names=set(z.namelist());require({'[Content_Types].xml','word/document.xml'}<=names,'unsupported_document');require(not any('..' in Path(x).parts or x.startswith('/') or 'vbaproject' in x.lower() for x in names),'unsafe_document');require(all(not(x.flag_bits&1) and x.file_size<=5000000 and x.file_size/max(1,x.compress_size)<=200 for x in info),'unsafe_document')
  except DomainError:raise
  except Exception:raise DomainError('corrupt_document') from None
  return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',{'container_validation':'zip_structure'}
 if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE':return 'audio/wav',{'container_validation':'header_only'}
 if raw.startswith(b'ID3') or len(raw)>2 and raw[0]==255 and raw[1]&224==224:return 'audio/mpeg',{'container_validation':'header_only'}
 if len(raw)>=12 and raw[4:8]==b'ftyp':return 'video/mp4',{'container_validation':'header_only'}
 if raw.startswith(b'OggS'):return 'audio/ogg',{'container_validation':'header_only'}
 raise DomainError('unsupported_media',415)

class Vault:
 def __init__(self,store,root,actor,ctx=None):self.store=store;self.root=Path(root);self.actor=actor;self.tenant=store.tenant(actor);self.ctx=ctx
 def _path(self,id):
  identifier(id);return self.root/self.tenant/(id+'.enc')
 def read(self,id):
  require(self.ctx is not None,'vault_context_required',503);meta=self.ctx.get('legal_evidence',id);p=self._path(id);require(not p.is_symlink() and p.is_file(),'evidence_unavailable',404)
  require(28<=p.stat().st_size<=MAX_FILE+28,'evidence_integrity_failed',503)
  sealed=p.read_bytes()
  try:raw=self.store.aes.decrypt(sealed[:12],sealed[12:],('legal.file:'+self.tenant+':'+id).encode())
  except Exception:raise DomainError('evidence_integrity_failed',503) from None
  require(len(raw)==meta['size_bytes'] and hashlib.sha256(raw).hexdigest()==meta['sha256'],'evidence_integrity_failed',503);return meta,raw
 def upload(self,case_id,name,raw):
  mime,dims=sniff(raw);id=str(uuid4());name=re.sub(r'[^\w. ()-]','_',Path(name or 'evidence').name)[:120] or 'evidence'
  folder=self._path(id).parent;folder.mkdir(parents=True,exist_ok=True,mode=0o700);p=self._path(id);nonce=os.urandom(12)
  sealed=nonce+self.store.aes.encrypt(nonce,raw,('legal.file:'+self.tenant+':'+id).encode())
  def run(d):
   from .store import Context
   ctx=Context(self.store,d,self.actor);ctx.get('legal_case',case_id)
   existing=ctx.list('legal_evidence');require(sum(r['size_bytes'] for r in existing)+len(raw)<=100*1024*1024,'evidence_storage_quota',429)
   with open(p,'xb') as out:os.chmod(p,0o600);out.write(sealed)
   return ctx.create('legal_evidence',{'case_id':case_id,'file_id':id,'name':name,'mime_type':mime,'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'uploaded_at':now_iso(),'encryption':'AES-256-GCM','status':'ready','authenticity':'not_verified','content_analysis':'not_performed',**dims},id=id)
  try:return self.store.transaction(run)
  except BaseException:
   if p.exists():p.unlink()
   raise
