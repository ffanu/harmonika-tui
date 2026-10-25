"""Reviewable deployment artifacts. These handlers never mutate an external host."""
import json,re,shlex
from .safe import slug,domain,rel_path
NUMBERS={'deploy.vercel':109,'deploy.netlify':110,'deploy.cloudflare':111,'deploy.vps.docker':112,'deploy.vps.pm2':113,'deploy.vps.systemd':114,'deploy.kubernetes':115,'deploy.ssh.direct':116,'deploy.docker.compose':117,'deploy.nginx.config':118,'deploy.ssl.setup':119,'deploy.domain.dns':120,'deploy.cdn.setup':121,'deploy.rollback':122,'deploy.blue_green':123}
SCHEMA={'type':'object','properties':{'project':{'type':'string','pattern':'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$','minLength':2,'maxLength':50},'domain':{'type':'string','maxLength':253},'port':{'type':'integer','minimum':1024,'maximum':65534,'default':3000},'framework':{'type':'string','enum':['nextjs','fastapi','express'],'default':'nextjs'},'repository':{'type':'string','pattern':r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?','maxLength':200}},'required':['project'],'additionalProperties':False}
def emit(id,a):
 p=slug(a['project']);port=a.get('port',3000);framework=a.get('framework','nextjs');host=domain(a.get('domain','example.invalid'),'domain');writes=[];requires=['Operator-reviewed target and account configuration','Run actual build/test before applying to production'];base=p+'/deploy/';instructions='Generated configuration only. Nothing has been deployed or charged.\n'
 if id=='deploy.vercel':
  writes=[(p+'/vercel.json',json.dumps({'framework':'nextjs' if framework=='nextjs' else None,'regions':['sin1']},indent=2)+'\n')];requires+=['Registered Vercel target/token; approval of the deployment job'];instructions+='Use the Harmonika registered deployment adapter for a preview first. Provider READY must be checked separately.\n'
 elif id=='deploy.netlify':
  writes=[(p+'/netlify.toml','[build]\ncommand = "npm run build"\npublish = "dist"\n\n[[headers]]\nfor = "/api/*"\n[headers.values]\nCache-Control = "no-store"\n')];requires+=['Netlify site/token; choose the framework adapter and actual publish directory'];instructions+='Next.js requires its supported Netlify framework adapter; dist is a generic SPA directory, not a claim that a Next.js build lives there.\n'
 elif id=='deploy.cloudflare':
  writes=[(p+'/wrangler.json',json.dumps({'name':p,'pages_build_output_dir':'dist'},indent=2)+'\n')];requires+=['Cloudflare account/project/token','Static export or a supported Next.js/OpenNext adapter'];instructions+='Review build output before wrangler pages deploy. No DNS or account was modified.\n'
 elif id in ('deploy.vps.docker','deploy.docker.compose'):
  if framework=='fastapi':docker='FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nRUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app:app /app\nUSER app\nEXPOSE '+str(port)+'\nCMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","'+str(port)+'"]\n'
  else:docker='FROM node:22-bookworm-slim\nWORKDIR /app\nCOPY package*.json ./\nRUN npm ci --ignore-scripts --no-audit --no-fund\nCOPY . .\nRUN npm run build\nENV NODE_ENV=production\nUSER node\nEXPOSE '+str(port)+'\nCMD ["npm","start"]\n'
  compose='services:\n  web:\n    build: .\n    user: "10001:10001"\n    ports: ["127.0.0.1:'+str(port)+':'+str(port)+'"]\n    env_file: ["/etc/'+p+'/app.env"]\n    restart: unless-stopped\n    security_opt: ["no-new-privileges:true"]\n    cap_drop: ["ALL"]\n    read_only: true\n    tmpfs: ["/tmp:size=32m"]\n    volumes: ["app_data:/app/data"]\nvolumes:\n  app_data: {}\n'
  writes=[(p+'/Dockerfile',docker),(p+'/compose.yaml',compose),(p+'/.dockerignore','node_modules\n.git\n.env\n.env.*\n*.pem\n*.key\ndata/\n')];requires+=['Docker host with a scoped non-root deploy account','Configure actual app entrypoint/UID and runtime secrets']
 elif id=='deploy.vps.pm2':
  writes=[(base+'ecosystem.config.cjs','module.exports = '+json.dumps({'apps':[{'name':p,'cwd':'/srv/'+p+'/current','script':'npm','args':'start','instances':1,'env':{'NODE_ENV':'production','PORT':port}}]},indent=2)+';\n')];requires+=['PM2 installed under the dedicated application account']
 elif id=='deploy.vps.systemd':
  command='/srv/'+p+'/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port '+str(port) if framework=='fastapi' else '/usr/bin/npm start'
  unit='[Unit]\nDescription='+p+' application\nAfter=network.target\n[Service]\nUser=builder\nWorkingDirectory=/srv/'+p+'/current\nEnvironmentFile=/etc/'+p+'/app.env\nEnvironment=PORT='+str(port)+'\nExecStart='+command+'\nRestart=on-failure\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectSystem=strict\nProtectHome=yes\nReadWritePaths=/srv/'+p+'/data\n[Install]\nWantedBy=multi-user.target\n'
  writes=[(base+p+'.service',unit)];requires+=['Dedicated builder user, venv/runtime and reviewed service install']
 elif id=='deploy.kubernetes':
  yaml='apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: '+p+'\nspec:\n  replicas: 1\n  selector:\n    matchLabels: {app: '+p+'}\n  template:\n    metadata:\n      labels: {app: '+p+'}\n    spec:\n      securityContext: {runAsNonRoot: true, runAsUser: 10001}\n      containers:\n        - name: web\n          image: registry.invalid/'+p+':reviewed\n          ports: [{containerPort: '+str(port)+'}]\n          envFrom: [{secretRef: {name: '+p+'-runtime}}]\n          resources:\n            requests: {cpu: 100m, memory: 128Mi}\n            limits: {cpu: "1", memory: 512Mi}\n          securityContext:\n            allowPrivilegeEscalation: false\n            capabilities: {drop: [ALL]}\n---\napiVersion: v1\nkind: Service\nmetadata: {name: '+p+'}\nspec:\n  selector: {app: '+p+'}\n  ports: [{port: 80, targetPort: '+str(port)+'}]\n'
  writes=[(base+'kubernetes.yaml',yaml)];requires+=['Cluster context, published image digest, namespace/runtime Secret and explicit approval']
 elif id=='deploy.ssh.direct':
  repo=a.get('repository');instructions+='Upload a versioned artifact using the registered SSH account; do not git pull into a live mutable release.\n'
  if repo:instructions+='Review repository '+repo+' and pin its commit before creating a release.\n'
  writes=[(base+'ssh-release-plan.json',json.dumps({'release_root':'/srv/'+p+'/releases','current':'/srv/'+p+'/current','repository':repo,'requires_artifact_sha256':True,'mutate_live_checkout':False},indent=2)+'\n')]
 elif id=='deploy.nginx.config':
  cfg='server {\n  listen 80;\n  server_name '+host+';\n  client_max_body_size 1m;\n  location / {\n    proxy_pass http://127.0.0.1:'+str(port)+';\n    proxy_http_version 1.1;\n    proxy_set_header Host $host;\n    proxy_set_header X-Real-IP $remote_addr;\n    proxy_set_header X-Forwarded-Proto $scheme;\n    proxy_read_timeout 180s;\n    proxy_buffering off;\n  }\n  location ~ /\\. { deny all; }\n}\n'
  writes=[(base+'nginx.conf',cfg)];requires+=['Review server_name and existing config; nginx -t before reload']
 elif id=='deploy.ssl.setup':
  script='#!/bin/sh\nset -eu\n# Run only after verifying DNS, port80 and the isolated site config.\ncertbot --nginx --domain '+shlex.quote(host)+'\ncertbot renew --dry-run\n'
  writes=[(base+'ssl-setup.sh',script)];requires+=['Owned domain, DNS pointing to target, Certbot, ACME account consent']
 elif id=='deploy.domain.dns':
  writes=[(base+'dns-plan.json',json.dumps({'zone':host,'record':{'type':'A','name':host,'content':'OPERATOR_PUBLIC_IPV4_REQUIRED','ttl':300},'applied':False},indent=2)+'\n')];requires+=['Authorized DNS account/zone and actual public target IP']
 elif id=='deploy.cdn.setup':
  writes=[(base+'cdn-policy.json',json.dumps({'domain':host,'cache_static_extensions':['css','js','woff2','png','webp'],'bypass_paths':['/api/*','/auth/*','/files/*'],'respect_origin_cache_control':True,'cache_private_responses':False,'applied':False},indent=2)+'\n')];requires+=['Authorized CDN zone/rules API; verify authenticated paths are not cached']
 elif id=='deploy.rollback':
  script='#!/bin/sh\nset -eu\n# Supply an already-tested release directory basename; never arbitrary paths.\nRELEASE=${1:?release required}\ncase "$RELEASE" in ""|*[!a-zA-Z0-9_-]*) exit 2;; esac\nROOT=/srv/'+p+'\ntest -d "$ROOT/releases/$RELEASE"\nln -s "releases/$RELEASE" "$ROOT/current.next"\nmv -Tf "$ROOT/current.next" "$ROOT/current"\n# Restart only the reviewed application service, then run its health check.\n'
  writes=[(base+'rollback.sh',script)];requires+=['Prior immutable release, database compatibility check, application-scoped restart approval']
 elif id=='deploy.blue_green':
  writes=[(base+'blue-green.json',json.dumps({'app':p,'blue':'127.0.0.1:'+str(port),'green':'127.0.0.1:'+str(port+1),'procedure':['build immutable candidate','run candidate health and smoke','validate proxy config','switch only this site upstream','observe errors','switch back on failed health'],'cutover_performed':False},indent=2)+'\n')];requires+=['Two healthy app slots and a site-scoped proxy switch with rollback']
 else:raise ValueError('unknown_deployment_skill')
 writes.append((base+id.replace('.','-')+'.md',instructions+'\nPrerequisites:\n'+'\n'.join('- '+r for r in requires)+'\n'))
 return {'data':{'provider_operation':id,'project':p,'executed':False,'deployed':False,'requires_configuration':requires},'writes':writes,'limitations':['Operator-reviewed artifacts only; no cloud/SSH/DNS/service mutation occurred.']}
SKILLS={id:{'number':n,'summary':id+' deployment configuration','schema':SCHEMA,'handler':(lambda a,skill=id:emit(skill,a))} for id,n in NUMBERS.items()}
