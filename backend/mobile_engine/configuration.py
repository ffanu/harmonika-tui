"""Small source-only configuration generators; no CI, install or environment mutation."""
import copy,json,re
from urllib.parse import urlsplit
IDS=('scaffold.env_config','scaffold.ci_cd.mobile','scaffold.linting.mobile')
SCHEMA={'type':'object','additionalProperties':False,'properties':{'identifier':{'type':'string','pattern':'^[a-z][a-z0-9]*(?:\\.[a-z][a-z0-9]*){1,5}$','default':'id.harmonika.app'},'api_base_url':{'type':'string','maxLength':500,'default':'https://example.invalid/api'}}}
def descriptors():return {id:{'description':'Menghasilkan konfigurasi source Expo; tidak menjalankan CI atau installer.','input_schema':copy.deepcopy(SCHEMA),'mode':'source_generation'} for id in IDS}
def dispatch(id,v):
 if id not in IDS or type(v) is not dict or set(v)-set(SCHEMA['properties']):raise ValueError('invalid_input')
 identifier=v.get('identifier','id.harmonika.app');url=v.get('api_base_url','https://example.invalid/api')
 if type(identifier) is not str or len(identifier)>120 or not re.fullmatch(r'[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){1,5}',identifier):raise ValueError('invalid_identifier')
 if type(url) is not str or len(url)>500 or any(ord(c)<33 or ord(c)>126 or c in '\"\'`$\\' for c in url):raise ValueError('invalid_api_url')
 parsed=urlsplit(url)
 if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('invalid_api_url')
 files=[]
 if id=='scaffold.env_config':
  files=[{'path':'.env.example','content':'# Public API origin only; never place a provider secret in EXPO_PUBLIC variables.\nEXPO_PUBLIC_API_BASE_URL='+url+'\n'},
   {'path':'src/config.ts','content':'''const configured = process.env.EXPO_PUBLIC_API_BASE_URL;
export function apiBaseURL(): string {
  if (!configured) throw new Error('API base URL belum dikonfigurasi.');
  const url = new URL(configured);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw new Error('Konfigurasi URL tidak aman.');
  return url.toString().replace(/\\/$/, '');
}
'''}]
 elif id=='scaffold.linting.mobile':
  files=[{'path':'eslint.config.js','content':"const { defineConfig } = require('eslint/config');\nconst expoConfig = require('eslint-config-expo/flat');\nmodule.exports = defineConfig([expoConfig, { ignores: ['dist/*'] }]);\n"},
   {'path':'tsconfig.json','content':json.dumps({'extends':'expo/tsconfig.base','compilerOptions':{'strict':True,'noUncheckedIndexedAccess':True}},indent=2)+'\n'}]
 else:
  files=[{'path':'.github/workflows/mobile-check.yml','content':'''name: Mobile source checks
on:
  workflow_dispatch:
  pull_request:
permissions:
  contents: read
jobs:
  expo-source:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
      - run: npm ci --ignore-scripts
      - run: npx --no-install tsc --noEmit
'''}]
 files.append({'path':'docs/mobile-configuration.md','content':'# Source configuration\n\nReview sebelum menerapkan. Framework: Expo TypeScript. Install dependency kompatibel SDK dan commit lockfile di proyek sendiri.\neslint-config-expo diperlukan untuk lint config. CI hanya pemeriksaan source dan belum dijalankan, tanpa publish/store/signing.\nGunakan environment dev/staging/production di pipeline berizin. API bisnis tetap memvalidasi token dan authorization.\n'})
 return {'status':'completed','mode':'source_generation','files':files,'warnings':['Konfigurasi dihasilkan; belum dipasang, dieksekusi, atau diuji pada aplikasi pengguna.'],'runtime_verified':False}
