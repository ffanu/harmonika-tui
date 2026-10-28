"""Download pinned public source archives using the selected wired interface."""
import hashlib,http.client,ipaddress,json,os,shutil,stat,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCES=[('launcher-source','ThomasEricB/minimax-h3-pinokio','5fd0b65ad0d455892f573e50c8dfcb29799cc60d'),('ComfyUI','comfyanonymous/ComfyUI','426f5eea52a7ece1dd599fff4cb6cd1f352ddd24')]
def main():
 address=json.loads((ROOT/'download-interface.json').read_text())['source_ip'];ip=ipaddress.ip_address(address);assert ip.version==4 and ip.is_private
 class Wired(http.client.HTTPSConnection):
  def __init__(self,*a,**kw):super().__init__(*a,source_address=(address,0),**kw)
 class Handler(urllib.request.HTTPSHandler):
  def https_open(self,req):return self.do_open(Wired,req,context=self._context)
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}),Handler());receipt=[]
 for folder,repo,revision in SOURCES:
  dest=ROOT/folder;pin=ROOT/(folder+'-source-receipt.json')
  if dest.exists():
   assert pin.exists() and json.loads(pin.read_text())['revision']==revision,'unverified_source_directory'
   receipt.append(json.loads(pin.read_text()));continue
  url='https://codeload.github.com/'+repo+'/zip/'+revision
  archive=ROOT/(folder+'.zip');pinrow=next(x for x in json.loads((ROOT/'sources.json').read_text()) if x['folder']==folder)
  if archive.exists():data=archive.read_bytes()
  else:
   with op.open(url,timeout=90) as response:data=response.read(100*1024*1024+1)
   archive.write_bytes(data)
  assert len(data)==pinrow['bytes'] and hashlib.sha256(data).hexdigest()==pinrow['sha256'],'source_archive_checksum_mismatch'
  temporary=ROOT/(folder+'-extract');temporary.mkdir(exist_ok=False)
  with zipfile.ZipFile(archive) as z:
   entries=z.infolist();assert len(entries)<=30000 and sum(i.file_size for i in entries)<=300*1024*1024
   for entry in entries:
    assert (temporary/entry.filename).resolve().is_relative_to(temporary.resolve()) and not stat.S_ISLNK(entry.external_attr>>16),'unsafe_source_archive'
   z.extractall(temporary)
  children=list(temporary.iterdir());assert len(children)==1 and children[0].is_dir();children[0].rename(dest);temporary.rmdir()
  item={'repo':repo,'revision':revision,'archive_sha256':hashlib.sha256(data).hexdigest()};pin.write_text(json.dumps(item,indent=2));receipt.append(item)
  print(json.dumps({'source_verified':folder,'revision':revision}),flush=True)
 (ROOT/'source-receipt.json').write_text(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
