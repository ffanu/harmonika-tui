"""Monitoring/maintenance configuration, never background host mutation."""
import json
from .safe import slug,http_url
NUMBERS={'monitor.uptime':152,'monitor.error.track':153,'monitor.performance':154,'monitor.logs':155,'monitor.alerts':156,'maintain.backup':157,'maintain.update':158,'maintain.security.patch':159,'maintain.cdn_cache':160,'maintain.scale':161}
SCHEMA={'type':'object','properties':{'project':{'type':'string','minLength':2,'maxLength':50,'pattern':'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$'},'url':{'type':'string','maxLength':2048},'interval_seconds':{'type':'integer','minimum':60,'maximum':86400,'default':300},'max_replicas':{'type':'integer','minimum':1,'maximum':10,'default':2}},'required':['project'],'additionalProperties':False}
BACKUP='''import datetime,os,pathlib,sqlite3
source=pathlib.Path(os.environ.get("APP_DATABASE","data/app.sqlite"))
if not source.is_file():raise SystemExit("database_missing")
root=pathlib.Path("backups");root.mkdir(mode=0o700,exist_ok=True)
name=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".sqlite"
target=root/name
src=sqlite3.connect("file:"+str(source.resolve())+"?mode=ro",uri=True);dst=sqlite3.connect(target)
try:
 src.backup(dst)
 check=dst.execute("PRAGMA integrity_check").fetchone()
 if check!=("ok",):raise RuntimeError("backup_integrity_failed")
finally:dst.close();src.close()
os.chmod(target,0o600)
print("backup_created:"+name)
'''
def emit(id,a):
 p=slug(a['project']);url=http_url(a.get('url','https://example.invalid/healthz'),'url');interval=a.get('interval_seconds',300);base=p+'/ops/';writes=[];requirements=['Review and configure the actual project/target before enabling a schedule.']
 if id in ('monitor.uptime','monitor.performance'):
  script='import json,time,urllib.request\nurl='+repr(url)+'\nstarted=time.monotonic()\nreq=urllib.request.Request(url,headers={"User-Agent":"Harmonika-Project-Monitor"})\nwith urllib.request.urlopen(req,timeout=10) as response:\n status=response.status\n body=response.read(1048577)\nprint(json.dumps({"http_status":status,"response_bytes_capped":min(len(body),1048576),"elapsed_ms":round((time.monotonic()-started)*1000,2)}))\n'
  writes=[(base+'health_probe.py',script),(base+'monitor-schedule.json',json.dumps({'interval_seconds':interval,'enabled':False,'source':'health_probe.py','metrics':['http_status','elapsed_ms'],'browser_core_web_vitals_measured':False},indent=2)+'\n')]
 elif id=='monitor.error.track':
  writes=[(base+'sentry-init.cjs','const Sentry = require("@sentry/node");\nconst dsn=process.env.SENTRY_DSN;\nif(!dsn)throw Error("SENTRY_DSN_required");\nSentry.init({dsn,sendDefaultPii:false,beforeSend(event){delete event.user;if(event.request){delete event.request.data;delete event.request.cookies;delete event.request.headers;}return event;}});\n')];requirements+=['Install and pin the Sentry SDK; configure a project DSN outside source.']
 elif id=='monitor.logs':
  writes=[(base+'logging-policy.json',json.dumps({'structured':True,'allowed_fields':['timestamp','level','event','request_id','status','duration_ms'],'never_log':['authorization','cookie','password','full_prompt','customer_media','request_body'],'retention_days':7,'enabled':False},indent=2)+'\n')];requirements+=['Wire the policy into the application logger and verify redaction before collecting production logs.']
 elif id=='monitor.alerts':
  writes=[(base+'alert-rules.yaml','groups:\n  - name: '+p+'-health\n    rules:\n      - alert: ApplicationUnavailable\n        expr: up{job="'+p+'"} == 0\n        for: 5m\n        labels: {severity: warning}\n        annotations: {summary: "Application health probe unavailable"}\n')];requirements+=['Prometheus scrape target and an approved Alertmanager receiver; no notification was sent.']
 elif id=='maintain.backup':
  writes=[(base+'backup_sqlite.py',BACKUP),(base+'backup-schedule.json',json.dumps({'enabled':False,'utc_schedule':'0 2 * * *','command':'python3 ops/backup_sqlite.py','verify_restore_before_retention_cleanup':True,'delete_backups_automatically':False},indent=2)+'\n')];requirements+=['SQLite APP_DATABASE path, private backup storage and a measured restore test.']
 elif id in ('maintain.update','maintain.security.patch'):
  writes=[(p+'/.github/dependabot.yml','version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: "/"\n    schedule: {interval: weekly}\n    open-pull-requests-limit: 3\n  - package-ecosystem: pip\n    directory: "/"\n    schedule: {interval: weekly}\n    open-pull-requests-limit: 3\n'),(base+'dependency-update-policy.json',json.dumps({'automatic_merge':False,'automatic_production_deploy':False,'require_tests_lint_typecheck':True,'native_harmonika_update':False,'operator_review_required':True},indent=2)+'\n')];requirements+=['Enable repository dependency PRs; review licenses/security and run QA before merging.']
 elif id=='maintain.cdn_cache':
  writes=[(base+'cache-invalidation-plan.json',json.dumps({'enabled':False,'strategy':'versioned_static_assets','purge_scope':'explicit_urls_only','purge_everything':False,'private_api_cache':False,'requires_zone_and_permission':True},indent=2)+'\n')];requirements+=['Registered CDN zone/token; select exact asset URLs, never purge another site.']
 elif id=='maintain.scale':
  maximum=a.get('max_replicas',2);writes=[(base+'autoscaling.yaml','apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata: {name: '+p+'}\nspec:\n  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: '+p+'}\n  minReplicas: 1\n  maxReplicas: '+str(maximum)+'\n  metrics:\n    - type: Resource\n      resource:\n        name: cpu\n        target: {type: Utilization, averageUtilization: 70}\n'),(base+'scaling-approval.json',json.dumps({'applied':False,'budget_authorized':False,'max_replicas':maximum,'requires_cluster_metrics':True},indent=2)+'\n')];requirements+=['Cluster context, metrics-server, measured resource requests and explicit budget authorization.']
 else:raise ValueError('unknown_monitor_skill')
 return {'data':{'operation':id,'executed':False,'scheduled':False,'requires_configuration':requirements},'writes':writes,'limitations':['Configuration/artifact generation only. No monitor, notification, update, purge, backup schedule or scaling was activated.']}
SKILLS={id:{'number':n,'summary':id+' monitoring or maintenance configuration','schema':SCHEMA,'handler':lambda a,skill=id:emit(skill,a)} for id,n in NUMBERS.items()}
