"""200 actual HTTP attempts with coordinator stopped; technical output only."""
import base64,collections,hashlib,json,os,threading,urllib.request,urllib.error
from pathlib import Path
label=os.environ['LEVEL9_LABEL'];barrier=threading.Barrier(200);lock=threading.Lock();rows=[];active=0;maximum=0
payload={'text':'burst synthetic','tasks':[{'name':'prepare','needs':[]},{'name':'publish','needs':['prepare']}]}
def one(i):
 global active,maximum
 owner='alpha' if i%2==0 else 'beta';key='burst-'+label+'-'+str(i%20);barrier.wait()
 with lock:active+=1;maximum=max(maximum,active)
 began=__import__('time').monotonic();req=urllib.request.Request('http://127.0.0.1:18081/workflows',headers={'X-Lab-Owner':owner,'Content-Type':'application/json','Idempotency-Key':key},data=json.dumps(payload).encode(),method='POST')
 try:
  try:r=urllib.request.urlopen(req,timeout=10)
  except urllib.error.HTTPError as e:r=e
  with r:status=r.status;value=json.loads(r.read());id=value.get('workflow',{}).get('id')
 except Exception:status,id=0,None
 with lock:active-=1;rows.append({'latency_ms':round((__import__('time').monotonic()-began)*1000,3),'status':status,'id':id,'owner':owner,'key_sha256':hashlib.sha256(key.encode()).hexdigest()})
ts=[threading.Thread(target=one,args=(i,)) for i in range(200)]
for t in ts:t.start()
for t in ts:t.join(12)
accepted={};groups=collections.defaultdict(set)
for row in rows:
 if row['status'] in [200,202] and isinstance(row['id'],str):accepted[row['id']]=row['owner'];groups[(row['owner'],row['key_sha256'])].add(row['id'])
initial_accepted=len(accepted);initial_per_owner=dict(collections.Counter(accepted.values()));fill=[];quota={}
def request_api(path,owner,method='GET',key=None):
 headers={'X-Lab-Owner':owner,'Content-Type':'application/json'}
 if key:headers['Idempotency-Key']=key
 req=urllib.request.Request('http://127.0.0.1:18081'+path,headers=headers,data=json.dumps(payload).encode() if method=='POST' else None,method=method)
 try:r=urllib.request.urlopen(req,timeout=10)
 except urllib.error.HTTPError as e:r=e
 with r:return r.status,json.loads(r.read())
for owner in ['alpha','beta']:
 candidates=[i for i in range(20) if ('alpha' if i%2==0 else 'beta')==owner]
 attempts=0
 for i in candidates:
  if sum(x==owner for x in accepted.values())>=4 or attempts>=4:break
  key='burst-'+label+'-'+str(i);h=hashlib.sha256(key.encode()).hexdigest();initial=[x for x in rows if x['owner']==owner and x['key_sha256']==h]
  if not initial or not any(x['status']==503 for x in initial) or any(x['status'] not in [429,503] for x in initial):continue
  status,value=request_api('/workflow-requests/'+key,owner)
  if status!=404:continue
  status,value=request_api('/workflows',owner,'POST',key);attempts+=1;fill.append({'owner':owner,'key_sha256':h,'prior_lookup_status':404,'status':status})
  if status in [200,202]:accepted[value['workflow']['id']]=owner
 status,_=request_api('/workflows',owner,'POST','quota-'+label);quota[owner]=status
raw=json.dumps({'initial':rows,'fill':fill,'quota':quota},sort_keys=True,separators=(',',':')).encode();p=Path('/data/proofs');p.mkdir(exist_ok=True);(p/('workflow-burst-'+label+'.json')).write_bytes(raw)
latencies=sorted(r['latency_ms'] for r in rows)
print(json.dumps({'latency_ms':{'p50':latencies[len(latencies)//2],'p95':latencies[int((len(latencies)-1)*.95)],'max':max(latencies)},'request_count':len(rows),'initial_distinct_accepted':initial_accepted,'initial_per_owner':initial_per_owner,'fill_calls':fill,'quota_probe_status':quota,'max_overlapping_clients':maximum,'status_counts':dict(collections.Counter(str(x['status']) for x in rows)),'accepted_workflows':[{'id':id,'owner':owner} for id,owner in sorted(accepted.items())],'per_owner_accepted':dict(collections.Counter(accepted.values())),'idempotency_converged':all(len(x)==1 for x in groups.values()),'proof_sha256':hashlib.sha256(raw).hexdigest()}))
