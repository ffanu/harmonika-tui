"""Canonical 214-ID catalog; implementation discovery never enables a connector."""
import copy,hashlib,json,re,importlib.util
from pathlib import Path,PurePosixPath
from . import planning,configuration,native_modules,flutter_modules
ROOT=Path(__file__).parent
SOURCE=json.loads((ROOT/'catalog_source.json').read_text())
CANONICAL={x['id']:x for x in SOURCE['skills']}
BLOCKED_PREFIX=('build.','deploy.','ota.','iap.','test.','monitor.','analytics.','cicd.','bridge.publish.')
def implementations():
 out={**planning.descriptors(),**configuration.descriptors(),**native_modules.descriptors(),**flutter_modules.descriptors()}
 if importlib.util.find_spec(__package__+'.generators') is not None:
  from . import generators
  for id,d in generators.descriptors().items():
   if not generators.support(id):continue
   if id not in CANONICAL or id in out or id.startswith(BLOCKED_PREFIX):raise ValueError('invalid_generator_registration')
   out[id]={**d,'mode':'source_generation'}
 return out

def descriptors():
 impl=implementations();rows=[]
 for id,item in CANONICAL.items():
  available=id in impl
  reason=None if available else 'implementation_missing'
  if not available and id.startswith(('build.','test.')):reason='registered_mobile_toolchain_required'
  if not available and id.startswith(('deploy.','iap.','ota.','monitor.','analytics.','cicd.','bridge.publish.')):reason='authorized_connector_and_runtime_required'
  rows.append({**copy.deepcopy(item),'status':'available' if available else 'unavailable','mode':impl[id]['mode'] if available else None,'reason':reason,'execution_target':'bounded_mobile_source' if available else 'not_configured',**({'input_schema':copy.deepcopy(impl[id]['input_schema'])} if available else {})})
 return rows

def capabilities():
 rows=descriptors();modes={k:sum(r['mode']==k for r in rows) for k in ('planning','source_generation')}
 return {'schema_version':1,'engine':'harmonika-mobile-builder','source_sha256':SOURCE['source_sha256'],'declared_header_count':SOURCE['header_declared'],'catalog_size':len(rows),'readiness':{'available':sum(modes.values()),'unavailable':len(rows)-sum(modes.values()),**modes},'features':{'spec_analysis':True,'source_generation':modes['source_generation']>0,'android_build':False,'ios_build':False,'code_signing':False,'store_publish':False,'ota_release':False,'device_farm':False,'live_iap':False,'host_filesystem_access':False},'limits':{'spec_characters':32000,'input_bytes':65536,'output_bytes':262144,'files':60,'file_bytes':65536,'timeout_seconds':15,'global_active_shared':2,'per_actor_active_shared':1},'registration_is_not_runtime_readiness':True}

def verify_files(out):
 files=out.get('files',[])
 if type(files) is not list or len(files)>60:raise ValueError('invalid_generated_files')
 paths=set()
 for f in files:
  if type(f) is not dict or set(f)-{'path','content'} or type(f.get('path')) is not str or type(f.get('content')) is not str:raise ValueError('invalid_generated_file')
  path=f['path'];p=PurePosixPath(path)
  if len(path)>200 or '\\' in path or '\x00' in path or any(ord(c)<32 for c in path) or p.is_absolute() or any(x in ('..','.','') for x in path.split('/')) or path in paths:raise ValueError('invalid_generated_path')
  if p.suffix.lower() in ('.jks','.keystore','.p12','.pfx','.p8','.pem','.key','.mobileprovision') or (p.name.startswith('.env') and p.name!='.env.example') or p.name in ('.env','credentials.json','google-services.json','GoogleService-Info.plist'):raise ValueError('generated_secret_file_rejected')
  if len(f['content'].encode())>65536:raise ValueError('generated_file_limit')
  paths.add(path)
 return out

def dispatch(id,v):
 impl=implementations()
 if id not in impl:raise ValueError('implementation_missing')
 if type(v) is not dict or len(json.dumps(v,ensure_ascii=False,allow_nan=False).encode())>65536:raise ValueError('input_limit')
 if id in planning.IDS:result=planning.dispatch(id,v)
 elif id in configuration.IDS:result=configuration.dispatch(id,v)
 elif id in native_modules.IDS:result=native_modules.dispatch(id,v)
 elif id in flutter_modules.descriptors():result=flutter_modules.dispatch(id,v)
 else:
  from .generators import dispatch as generate
  result=generate(id,v)
 if type(result) is not dict or result.get('status')!='completed':raise ValueError('invalid_generator_result')
 mode=impl[id]['mode']
 result=verify_files({**result,'mode':mode,'execution_kind':mode,'runtime_verified':False,'build_performed':False,'tests_performed':False,'signed':False,'deployed':False,'external_actions_performed':False})
 encoded=json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
 if len(encoded)>262144:raise ValueError('output_limit')
 return result
