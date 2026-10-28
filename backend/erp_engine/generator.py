"""Build source artifacts only: no package install, remote command or production write."""
import base64,hashlib,io,json,re,zipfile
from pathlib import Path
from .planner import plan,require,ERPError
from .schema import generate_schema
ROOT=Path(__file__).parent
MAX_ARTIFACT=1024*1024

def template_files():
 return {path.relative_to(ROOT/'templates').as_posix():path.read_text() for path in sorted((ROOT/'templates').rglob('*')) if path.is_file() and '__pycache__' not in path.parts}
def starter_readme():return (ROOT/'starter-readme.txt').read_text()
def generate(spec,company='ERP Perusahaan',industry=None,optional=None,exclude=None,branding=None,components=None):
 p=plan(spec,company,industry,optional,exclude,branding,components)
 if p['status']!='planned':return p
 p['tenant_slug']=re.sub('[^a-z0-9]+','-',company.lower()).strip('-')[:60] or 'erp-'+hashlib.sha256(company.encode()).hexdigest()[:8]
 schema=generate_schema(p);files=template_files()
 files['apps/api/prisma/schema.prisma']=schema['prisma'];files['apps/api/prisma/tenant-policies.sql']=schema['tenant_policies_sql']
 files['apps/web/app/plan.json']=json.dumps(p,ensure_ascii=False,indent=2)
 files['docs/plan.json']=files['apps/web/app/plan.json'];files['docs/schema.mmd']=schema['erd']
 files['README.md']=starter_readme()
 files['docs/READINESS.json']=json.dumps({'status':'generated_source_unqualified','built':False,'runtime_core':p['implementation_boundary']['core_runtime'],'specialty_modules':[m['id'] for m in p['modules'] if m['runtime']!='core_starter'],'external_connectors':'unconfigured','deployment':'not_started','clinical_banking_network_operations':'not_implemented'},indent=2)
 files['source-manifest.json']=json.dumps({name:hashlib.sha256(raw.encode()).hexdigest() for name,raw in files.items()},indent=2)
 require(sum(len(v.encode()) for v in files.values())<=MAX_ARTIFACT,'source_artifact_too_large')
 buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
  for name,raw in files.items():
   info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o600<<16;archive.writestr(info,raw.encode())
 return {'status':'source_generated','plan':p,'files':files,'artifact':{'name':'harmonika-erp-source.zip','mime_type':'application/zip','sha256':hashlib.sha256(buffer.getvalue()).hexdigest(),'zip_base64':base64.b64encode(buffer.getvalue()).decode()},'built':False,'deployed':False,'tests_run':False,'needs_owner_configuration':True}
