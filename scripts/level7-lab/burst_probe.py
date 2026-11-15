"""100 actual barrier-synchronized HTTP clients; mechanical proof only."""
import collections,hashlib,http.client,json,os,socket,threading,time,urllib.request,urllib.error
from pathlib import Path
label=os.environ['LEVEL7_LABEL'];barrier=threading.Barrier(100);lock=threading.Lock();out=[];active=0;maximum=0
def http(path,method='GET',owner='alpha',key=None,body=None):
 headers={'X-Lab-Owner':owner,'Content-Type':'application/json'}
 if key:headers['Idempotency-Key']=key
 req=urllib.request.Request('http://127.0.0.1:18081'+path,headers=headers,method=method,data=json.dumps(body).encode() if body is not None else None)
 try:r=urllib.request.urlopen(req,timeout=12)
 except urllib.error.HTTPError as e:r=e
 with r:return r.status,json.loads(r.read())
def one(i):
 global active,maximum
 owner='alpha' if i%2==0 else 'beta';key='burst-'+label+'-'+str(i%20);barrier.wait()
 with lock:active+=1;maximum=max(maximum,active)
 try:
  status,body=http('/jobs','POST',owner,key,{'kind':'report','text':'burst fixture '+str(i%20)});id=body.get('job',{}).get('id');error=body.get('error')
 except Exception as e:status,id,error=0,None,'timeout' if isinstance(e,(TimeoutError,socket.timeout)) else 'connection' if isinstance(e,(OSError,http.client.HTTPException)) else 'invalid_response' if isinstance(e,ValueError) else 'other'
 with lock:active-=1;out.append({'owner':owner,'key_sha256':hashlib.sha256(key.encode()).hexdigest(),'status':status,'job_id':id,'error':error})
ts=[threading.Thread(target=one,args=(i,)) for i in range(100)]
for t in ts:t.start()
for t in ts:t.join(15)
groups=collections.defaultdict(set);accepted={}
for r in out:
 if r['status'] in [200,202] and isinstance(r['job_id'],str):groups[(r['owner'],r['key_sha256'])].add(r['job_id']);accepted[r['job_id']]=r['owner']
workers=set();states=collections.Counter();per_owner=collections.Counter()
for id,owner in accepted.items():
 status,value=http('/jobs/'+id,owner=owner)
 if status==200:
  job=value['job'];states[job['state']]+=1;per_owner[owner]+=job['state'] not in ['completed','cancelled','failed']
  if job.get('worker_id'):workers.add(job['worker_id'])
proof=json.dumps(out,sort_keys=True,separators=(',',':')).encode();folder=Path('/data/proofs');folder.mkdir(exist_ok=True);(folder/('burst-'+label+'.json')).write_bytes(proof)
print(json.dumps({'request_count':len(out),'max_overlapping_clients':maximum,'unique_request_keys':20,'status_counts':dict(collections.Counter(str(r['status']) for r in out)),'transport_error_counts':dict(collections.Counter(r['error'] for r in out if r['status']==0)),'accepted_jobs':[{'id':id,'owner':owner} for id,owner in sorted(accepted.items())],'idempotency_converged':all(len(v)==1 for v in groups.values()),'per_owner_pending':dict(per_owner),'observed_worker_ids':sorted(workers),'observed_states':dict(states),'proof_sha256':hashlib.sha256(proof).hexdigest()}))
