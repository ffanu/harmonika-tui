"""Protected fixed-root RecoveryManager RPC; no arbitrary code/path dispatch."""
import json,sys
from workflow_engine import WorkflowStore
from workflow_adapter import ChildClient
from recovery_engine import RecoveryManager
r=json.load(sys.stdin);op=r['operation'];owner=r.get('owner','alpha')
if owner not in ['alpha','beta','anonymous'] or op not in ['snapshot','restore','reconcile','health']:raise ValueError('invalid_recovery_rpc')
q=WorkflowStore('/data/workflows.sqlite',ChildClient(),schema_version=4)
m=RecoveryManager(q,'/data')
args={'snapshot':lambda:(owner,r['label']),'restore':lambda:(owner,r['label'],r['clone_label']),'reconcile':lambda:(owner,r['clone_label']),'health':lambda:()}[op]()
status,value=getattr(m,op)(*args);print(json.dumps({'http_status':status,'body':value}))
