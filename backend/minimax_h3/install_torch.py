"""Download exact official CUDA wheels over the wired adapter; install in own venv."""
import http.client,importlib.util,ipaddress,json,subprocess,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('h3_download',ROOT/'download_models.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.STATUS=ROOT/'torch-download-status.json'
def main():
 address=json.loads((ROOT/'download-interface.json').read_text())['source_ip'];ip=ipaddress.ip_address(address);assert ip.version==4 and ip.is_private
 class Wired(http.client.HTTPSConnection):
  def __init__(self,*args,**kw):super().__init__(*args,source_address=(address,0),**kw)
 class Handler(urllib.request.HTTPSHandler):
  def https_open(self,req):return self.do_open(Wired,req,context=self._context)
 urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}),Handler()))
 wheels=[]
 for item in json.loads((ROOT/'torch-wheels.json').read_text()):
  origin=urllib.parse.urlsplit(item['url'])
  assert origin.scheme=='https' and origin.hostname in ('download.pytorch.org','download-r2.pytorch.org') and not origin.username and not origin.password and origin.port in (None,443)
  assert '/' not in item['file'] and '..' not in item['file']
  target=ROOT/'wheels'/item['file'];m.download(item['url'],target,item['bytes'],item['sha256']);wheels.append(str(target))
 subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-m','pip','install','--no-index','--no-deps',*wheels],check=True)
 m.status('completed',sha256_verified=True,wheels=len(wheels))
if __name__=='__main__':main()
