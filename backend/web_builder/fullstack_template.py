"""Pinned offline full-stack starter. Generates files only, never runs installers."""
import hashlib,json,tarfile
from pathlib import Path,PurePosixPath
from .common import require
SHA='c41039da4901e04b34ab91cd1ef9e2e1b47bbe119ec535ebfe67bfae6e6287ae'
ARCHIVE=Path(__file__).with_name('assets')/'zai-1785226337571.tar'
SERVE='''import net from 'node:net';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';
export function checkPort(value) {
 if (!/^[0-9]+$/.test(String(value))) throw Error('invalid_port');
 const p=Number(value); if (!Number.isInteger(p)||p<1024||p>65535||p===3000) throw Error('invalid_or_forbidden_port');return p;
}
export async function freePort(value) {
 const port=value===undefined?0:checkPort(value);
 return await new Promise((resolve,reject)=>{
  const server=net.createServer();server.once('error',reject);
  server.listen(port,'127.0.0.1',()=>{const selected=server.address().port;server.close(()=>selected===3000?freePort().then(resolve,reject):resolve(selected));});
 });
}
export async function run(mode) {
 if (!['dev','start'].includes(mode)) throw Error('invalid_mode');
 const fixed=process.env.PORT;
 for(let attempt=0;attempt<8;attempt++) {
  const port=await freePort(fixed);let collision=false;
  const args=mode==='dev'?['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port',String(port)]:['.next/standalone/server.js'];
  const child=spawn(process.execPath,args,{stdio:['inherit','inherit','pipe'],env:{...process.env,PORT:String(port),HOSTNAME:'127.0.0.1'}});
  const forward=signal=>child.kill(signal);
  const term=()=>forward('SIGTERM'),int=()=>forward('SIGINT');
  process.once('SIGTERM',term);process.once('SIGINT',int);
  child.stderr.on('data',chunk=>{if(chunk.toString().includes('EADDRINUSE'))collision=true;process.stderr.write(chunk);});
  console.log(`Harmonika allocated http://127.0.0.1:${port}; wait for application readiness.`);
  let code;
  try {code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(c,s)=>resolve(s?1:(c??1)));});}
  finally {process.removeListener('SIGTERM',term);process.removeListener('SIGINT',int);}
  if(collision&&fixed===undefined)continue;
  return code;
 }
 throw Error('port_allocation_retries_exhausted');
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
 run(process.argv[2]||'dev').then(code=>{process.exitCode=code;}).catch(()=>{console.error('harmonika_start_failed');process.exitCode=1;});
}
'''
COPY_STATIC="""import fs from 'node:fs';
for (const [src,dest] of [['public','.next/standalone/public'],['.next/static','.next/standalone/.next/static']]) {
 if(fs.existsSync(src)) {fs.mkdirSync(dest,{recursive:true});fs.cpSync(src,dest,{recursive:true});}
}
"""
def generate(project):
 require(type(project) is str and 2<=len(project)<=50 and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in project) and project[0].isalpha(),'invalid_project')
 raw=ARCHIVE.read_bytes();require(len(raw)<=2097152 and hashlib.sha256(raw).hexdigest()==SHA,'template_integrity_failed',503);files={}
 with tarfile.open(ARCHIVE) as archive:
  members=archive.getmembers();require(len(members)<=200,'template_entry_limit',503)
  for entry in members:
   p=PurePosixPath(entry.name);require(not p.is_absolute() and '..' not in p.parts and (entry.isdir() or entry.isfile()),'unsafe_template_entry',503)
   name=str(p)
   if entry.isdir():continue
   # No upstream installers, proxy config, lockfiles, mini-services or test shells.
   if not (name.startswith(('src/','prisma/')) or name in ('package.json','tsconfig.json','components.json','postcss.config.mjs','tailwind.config.ts','eslint.config.mjs','.gitignore')):continue
   require(entry.size<=524288 and name not in files,'template_file_limit',503)
   files[name]=archive.extractfile(entry).read().decode('utf-8')
 package=json.loads(files['package.json']);package['name']=project;package['dependencies'].pop('z-ai-web-dev-sdk',None)
 package['scripts']={'dev':'node scripts/harmonika-serve.mjs dev','build':'next build && node scripts/copy-static.mjs','start':'node scripts/harmonika-serve.mjs start','lint':'eslint .','typecheck':'tsc --noEmit','db:generate':'prisma generate','db:migrate':'prisma migrate dev','db:deploy':'prisma migrate deploy'}
 files['package.json']=json.dumps(package,indent=2)+'\n';files['next.config.ts']='import type { NextConfig } from "next";\nconst config: NextConfig = {output:"standalone",reactStrictMode:true};\nexport default config;\n'
 files['src/app/layout.tsx']='''import type {Metadata} from "next";
import "./globals.css";
export const metadata: Metadata={title:"Harmonika Project",description:"Built with Harmonika AI"};
export default function Layout({children}:Readonly<{children:React.ReactNode}>) {return <html lang="id"><body>{children}</body></html>;}
'''
 files['src/app/page.tsx']='export default function Home(){return <main className="min-h-screen grid place-items-center p-6"><h1 className="text-3xl font-semibold">'+project+'</h1></main>;}\n'
 files['src/lib/db.ts']='''import {PrismaClient} from "@prisma/client";
const shared=globalThis as unknown as {prisma?:PrismaClient};
export const db=shared.prisma??new PrismaClient();
if(process.env.NODE_ENV!=="production")shared.prisma=db;
'''
 files['src/app/api/health/route.ts']='export function GET(){return Response.json({ok:true});}\n'
 files['scripts/harmonika-serve.mjs']=SERVE;files['scripts/copy-static.mjs']=COPY_STATIC
 files['.env.example']='DATABASE_URL="file:./project.db"\n# API credentials stay server-side; never NEXT_PUBLIC_*.\nHARMONIKA_AI_BASE_URL="https://chat.harmonika.id/v1"\nHARMONIKA_AI_API_KEY=""\n'
 files['deploy/nginx.example.conf']='''# Operator replaces placeholders only after project readiness; not auto-applied.
server {
 server_name __DOMAIN__;
 location / {
  proxy_pass http://127.0.0.1:__PORT__;
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
 }
}
'''
 files['TEMPLATE-PROVENANCE.json']=json.dumps({'upstream':'https://z-cdn.chatglm.cn/fullstack/code_1785226337571.tar','sha256':SHA,'adapted_by':'Harmonika AI','upstream_installers_included':False,'port_3000_forbidden':True,'ai_credentials_provisioned':False,'dependency_install_or_build_executed':False},indent=2)+'\n'
 files['README.md']='''# Harmonika full-stack project

Next.js + TypeScript + Tailwind/shadcn components + Prisma SQLite starter.
This is a scaffold: implement the requested business workflow before release.

1. Copy .env.example to .env; keep the project database isolated.
2. Install dependencies with npm install or bun install (not performed by generator).
3. Run npm run db:generate. Review schema before npm run db:migrate.
4. Run npm run typecheck, npm run lint, npm run build and your application tests.
5. Run npm run dev, or npm start after build. Launcher binds loopback on a free
   port, rejects 3000, checks explicit PORT and retries a startup bind collision.
   Allocation does not prove readiness; check /api/health before configuring proxy.
6. Operator supplies domain/TLS and renders deploy/nginx.example.conf using the
   allocated port. No DNS, TLS, proxy or production deployment is automated here.

HARMONIKA_AI_BASE_URL identifies the shared API. No AI key or feature integration
is silently provisioned. Review upstream dependency/security/license terms before
redistribution or production; attribution is in TEMPLATE-PROVENANCE.json.
'''
 return {'data':{'framework':'nextjs','template':'harmonika-fullstack','source_sha256':SHA,'port_strategy':'free_loopback_port_at_launch','forbidden_ports':[3000],'installed':False,'build_tested':False,'deployed':False},'writes':[(project+'/'+p,c) for p,c in sorted(files.items())],'limitations':['Offline artifact generation only. Dependencies, actual framework build, business features and domain provisioning remain separate verified steps.']}
