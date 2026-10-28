"""Strict internal contracts; no filesystem paths or arbitrary remote engine URLs."""
import base64,binascii,io,ipaddress,re,socket
from urllib.parse import urlsplit
OPS=('audio.transcribe','image.upscale','browser.inspect','audio.synthesize')
MAX_INPUT=12*1024*1024
class Failure(Exception):
 def __init__(self,code):self.code=code;super().__init__(code)
def require(test,code='invalid_input'):
 if not test:raise Failure(code)
def binary(value,limit=8*1024*1024):
 require(isinstance(value,str) and 0<len(value)<=4*((limit+2)//3),'invalid_media')
 try:data=base64.b64decode(value,validate=True)
 except (binascii.Error,ValueError):raise Failure('invalid_media') from None
 require(0<len(data)<=limit,'invalid_media');return data
def url(value,allowed_hosts,resolver=socket.getaddrinfo):
 require(isinstance(value,str) and len(value)<=2048,'invalid_url')
 try:p=urlsplit(value);port=p.port
 except ValueError:raise Failure('invalid_url') from None
 require(p.scheme=='https' and p.hostname in allowed_hosts and not p.username and not p.password and port in (None,443),'target_not_registered')
 require(not p.fragment and not p.query,'invalid_url')
 try:addresses=resolver(p.hostname,443,type=socket.SOCK_STREAM)
 except OSError:raise Failure('target_offline') from None
 require(bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses),'private_target_denied')
 return value
def validate(op,v,config):
 require(op in OPS,'skill_not_found');require(type(v) is dict)
 allowed={'audio.transcribe':{'audio_base64','language'},'image.upscale':{'image_base64'},'browser.inspect':{'url'},'audio.synthesize':{'text','reference_audio_base64','reference_text','language'}}[op]
 require(not set(v)-allowed)
 if op=='audio.transcribe':
  binary(v.get('audio_base64'));lang=v.get('language','auto');require(lang=='auto' or isinstance(lang,str) and re.fullmatch('[a-z]{2}',lang))
 elif op=='image.upscale':binary(v.get('image_base64'))
 elif op=='browser.inspect':url(v.get('url'),config.get('browser_allowed_hosts',[]))
 else:
  # A request cannot opt itself into research/commercial permission.
  require(config.get('tts_research_enabled') is True,'license_restricted')
  for key,limit in [('text',500),('reference_text',500)]:require(isinstance(v.get(key),str) and 1<=len(v[key])<=limit)
  require(v.get('language','en') in ('en','zh'),'language_unqualified')
  binary(v.get('reference_audio_base64'),2*1024*1024)
 return v

def validate_result(op,result):
 require(type(result) is dict,'invalid_engine_payload')
 if op=='audio.transcribe':
  require(type(result.get('transcript')) is str and len(result['transcript'])<=30000,'invalid_engine_payload')
  require(type(result.get('language')) is str and 2<=len(result['language'])<=8,'invalid_engine_payload')
 elif op=='image.upscale':
  data=binary(result.get('image_base64'),24*1024*1024)
  require(data[:8]==b'\x89PNG\r\n\x1a\n' and result.get('mime_type')=='image/png','invalid_engine_payload')
  require(result.get('scale')==4 and all(type(result.get(k)) is int and 256<=result[k]<=4096 for k in ('width','height')),'invalid_engine_payload')
 elif op=='audio.synthesize':
  data=binary(result.get('audio_base64'),8*1024*1024)
  require(data[:4]==b'RIFF' and data[8:12]==b'WAVE' and result.get('research_only') is True,'invalid_engine_payload')
 elif op=='browser.inspect':
  require(isinstance(result.get('page_text',''),str) and len(result.get('page_text',''))<=10000,'invalid_engine_payload')
  require(type(result.get('pages')) is list and len(result['pages'])<=10 and result.get('existing_profile_used') is False,'invalid_engine_payload')
  for page in result['pages']:require(type(page) is dict and type(page.get('title')) is str and len(page['title'])<=500 and type(page.get('url')) is str and len(page['url'])<=2048,'invalid_engine_payload')
 else:raise Failure('invalid_engine_payload')
 return result
