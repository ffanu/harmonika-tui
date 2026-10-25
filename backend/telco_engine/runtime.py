"""Exactly-once local requests, approvals and recovery independent of any mentor."""
import datetime as dt,hashlib,json,re,time
from uuid import uuid4
from .common import DomainError,require,now_iso,parse_time,validate
from .store import Store,Context
from .registry import loaded,catalog,DOCUMENT_IDS
TERMINAL=('completed','failed','stopped','expired','interrupted')
SCOPE={'kind':'isolated_tenant','company_data_connected':False,'live_network_actions':False,'external_payments_verified':False}
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
class Runtime:
    def __init__(self,path):self.store=Store(path);self.handlers,self.schemas,self.mutations,self.limitations=loaded()
    def recover(self):
        def run(d):
            d.execute('CREATE TABLE IF NOT EXISTS turns(actor TEXT NOT NULL,idem TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,result TEXT,PRIMARY KEY(actor,idem))')
            d.execute("UPDATE turns SET state='interrupted' WHERE state='running'")
            rows=d.execute("SELECT id FROM requests WHERE state='running'").fetchall()
            for r in rows:
                d.execute("UPDATE requests SET state='interrupted',error='service_restarted' WHERE id=?",(r['id'],));self._event(d,r['id'],'response.error',{'code':'service_restarted'})
        self.store.transaction(run)
    def capabilities(self):
        c=catalog();return {'schema_version':1,'document_skill_ids':276,'engines':25,'available_local_operations':len(self.handlers),'skills':c,'scope':SCOPE,'features':{'telco_engine':True,'telco_approval':True,'telco_request_recovery':True,'telco_events_replay':True,'telco_network_live':False,'telco_payment_gateway':False,'telco_kyc':False,'telco_customer_portal_login':False},'limits':{'input_bytes':262144,'records_per_tenant':10000,'query_rows_max':1000,'operations_per_minute':120,'approval_seconds':300,'requests_per_tenant':1000,'idempotency_retention_seconds':86400},'source_document':{'unique_ids':276,'duplicate_ids_removed':['fraud.usage.anomaly'],'header_claimed':320,'complete_document_read':True}}
    @staticmethod
    def _event(d,rid,type,data):
        seq=d.execute('SELECT coalesce(max(sequence),0)+1 FROM events WHERE request_id=?',(rid,)).fetchone()[0]
        require(seq<=128,'event_limit',429);d.execute('INSERT INTO events VALUES(?,?,?,?,?)',(rid,seq,type,now_iso(),canonical(data)))
    def _view(self,r):
        preview=json.loads(r['payload']) if r['state']=='approval_required' else None
        if preview and r['skill'].startswith('voucher.') and isinstance(preview.get('code'),str) and preview['code'].startswith('sha256:'):
            preview['code']='protected-voucher-ending-'+preview.pop('_code_last4','hidden')
        return {'id':r['id'],'skill_id':r['skill'],'status':r['state'],'input_hash':r['input_hash'],'created_at':r['created'],'expires_at':r['expires'],'approval_required':r['state']=='approval_required','approval_preview':preview,'result':json.loads(r['result']) if r['result'] else None,'error':{'code':r['error']} if r['error'] else None,'scope':SCOPE}
    def _row(self,d,a,rid):
        require(type(rid) is str and re.fullmatch('[0-9a-f-]{36}',rid) is not None,'invalid_request_id')
        r=d.execute('SELECT * FROM requests WHERE id=? AND actor=?',(rid,a)).fetchone();require(r is not None,'request_not_found',404);return r
    def _run(self,d,row,started=False):
        rid=row['id'];ctx=Context(d,row['tenant'],row['actor'],rid,row['skill'],read_only=row['skill'] not in self.mutations);d.execute('SAVEPOINT domain_action')
        if not started:self._event(d,rid,'tool.started',{'skill_id':row['skill']})
        try:
            out=self.handlers[row['skill']](ctx,json.loads(row['payload']))
            require(type(out) is dict,'invalid_domain_result',502)
            stored_out={**out,'codes':[],'codes_redacted':True,'one_time_only':True} if row['skill']=='voucher.generate' else out
            encoded=canonical(stored_out);require(len(encoded.encode())<=524288,'result_too_large',413)
            d.execute('RELEASE domain_action')
            if row['skill'] in self.mutations:d.execute('UPDATE tenants SET revision=revision+1 WHERE id=?',(row['tenant'],))
            d.execute("UPDATE requests SET state='completed',result=? WHERE id=?",(encoded,rid));self._event(d,rid,'response.completed',{'status':'completed','skill_id':row['skill']});ctx.event('request.completed',{'skill_id':row['skill']})
        except Exception as e:
            d.execute('ROLLBACK TO domain_action');d.execute('RELEASE domain_action')
            code=e.code if isinstance(e,DomainError) else 'domain_operation_failed'
            d.execute("UPDATE requests SET state='failed',error=? WHERE id=?",(code,rid));self._event(d,rid,'response.error',{'code':code});ctx.event('request.failed',{'error_code':code})
        view=self._view(self._row(d,row['actor'],rid))
        if row['skill']=='voucher.generate' and view['status']=='completed':view['result']=out;view['one_time_secret_material']=True
        return view
    def invoke(self,actor,skill,args,key):
        tenant=self.store.tenant(actor)
        require(type(skill) is str and skill in DOCUMENT_IDS,'unknown_skill',404)
        require(skill in self.handlers,'skill_unavailable',503)
        require(type(args) is dict,'invalid_input');validate(args,self.schemas[skill])
        if skill in ('voucher.validate','voucher.redeem'):
            code=args.get('code');require(type(code) is str and re.fullmatch('[A-Za-z0-9-]{8,64}',code) is not None,'invalid_voucher_code')
            args={**args,'code':'sha256:'+hashlib.sha256(code.encode()).hexdigest(),'_code_last4':code[-4:]}
        require(len(canonical(args).encode())<=262144,'input_too_large',413)
        require(type(key) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',key) is not None,'idempotency_required')
        fp=digest({'skill_id':skill,'input':args});now=now_iso();rid=str(uuid4())
        def run(d):
            old=d.execute('SELECT * FROM requests WHERE actor=? AND idem=?',(actor,key)).fetchone()
            if old:
                require(old['digest']==fp,'idempotency_conflict',409);return {**self._view(old),'replayed':True}
            # Bound control metadata; business records remain persistent.
            cutoff=(parse_time(now)-dt.timedelta(days=1)).isoformat().replace('+00:00','Z')
            expired=d.execute("SELECT id FROM requests WHERE actor=? AND state='approval_required' AND expires<?",(actor,now)).fetchall()
            for item in expired:
                d.execute("UPDATE requests SET state='expired',error='approval_expired' WHERE id=?",(item['id'],));self._event(d,item['id'],'response.error',{'code':'approval_expired'})
            oldids=[x[0] for x in d.execute("SELECT id FROM requests WHERE actor=? AND created<? AND state IN ('completed','failed','stopped','expired','interrupted')",(actor,cutoff))]
            for oldid in oldids:d.execute('DELETE FROM events WHERE request_id=?',(oldid,));d.execute('DELETE FROM requests WHERE id=?',(oldid,))
            minute=(parse_time(now)-dt.timedelta(minutes=1)).isoformat().replace('+00:00','Z')
            require(d.execute('SELECT count(*) FROM requests WHERE actor=? AND created>=?',(actor,minute)).fetchone()[0]<120,'rate_limited',429)
            require(d.execute('SELECT count(*) FROM requests WHERE tenant=?',(tenant,)).fetchone()[0]<1000,'request_quota',429)
            d.execute('INSERT OR IGNORE INTO tenants VALUES(?,0)',(tenant,));rev=d.execute('SELECT revision FROM tenants WHERE id=?',(tenant,)).fetchone()[0]
            state='approval_required' if skill in self.mutations else 'running';expires=(parse_time(now)+dt.timedelta(seconds=300)).isoformat().replace('+00:00','Z');ih=digest({'actor':actor,'skill':skill,'input':args,'revision':rev})
            d.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,actor,tenant,skill,canonical(args),fp,key,state,ih,rev,now,expires,None,None))
            self._event(d,rid,'response.created',{'status':state,'skill_id':skill})
            row=self._row(d,actor,rid)
            if state=='approval_required':return self._view(row)
            return self._run(d,row)
        return self.store.transaction(run)
    def status(self,a,rid):
        self.store.tenant(a)
        with self.store.db() as d:return self._view(self._row(d,a,rid))
    def approve(self,a,rid,input_hash):
        self.store.tenant(a)
        def run(d):
            r=self._row(d,a,rid)
            require(type(input_hash) is str and input_hash==r['input_hash'],'input_hash_mismatch',409)
            if r['state']=='completed':return {**self._view(r),'replayed':True}
            require(r['state']=='approval_required','approval_not_pending',409)
            rev=d.execute('SELECT revision FROM tenants WHERE id=?',(r['tenant'],)).fetchone()[0]
            error='approval_expired' if parse_time(now_iso())>parse_time(r['expires']) else 'stale_approval' if rev!=r['revision'] else None
            if error:
                d.execute("UPDATE requests SET state='expired',error=? WHERE id=?",(error,rid));self._event(d,rid,'response.error',{'code':error});return self._view(self._row(d,a,rid))
            d.execute("UPDATE requests SET state='running' WHERE id=?",(rid,));self._event(d,rid,'tool.started',{'skill_id':r['skill']})
            return None
        outcome=self.store.transaction(run)
        if outcome is not None:return outcome
        def commit(d):
            r=self._row(d,a,rid)
            if r['state']!='running':return self._view(r)
            revision=d.execute('SELECT revision FROM tenants WHERE id=?',(r['tenant'],)).fetchone()[0]
            if revision!=r['revision']:
                d.execute("UPDATE requests SET state='expired',error='stale_approval' WHERE id=?",(rid,));self._event(d,rid,'response.error',{'code':'stale_approval'});return self._view(self._row(d,a,rid))
            return self._run(d,r,started=True)
        return self.store.transaction(commit)
    def stop(self,a,rid):
        self.store.tenant(a)
        current=self.status(a,rid)
        if current['status'] in TERMINAL:return current
        require(current['status']=='approval_required','operation_already_committing',409)
        def run(d):
            r=self._row(d,a,rid)
            if r['state'] in TERMINAL:return self._view(r)
            require(r['state']=='approval_required','operation_already_committing',409)
            d.execute("UPDATE requests SET state='stopped' WHERE id=?",(rid,));self._event(d,rid,'response.stopped',{'status':'stopped'});return self._view(self._row(d,a,rid))
        return self.store.transaction(run)
    def events(self,a,rid,after=0):
        self.store.tenant(a);require(type(after) is int and after>=0,'invalid_sequence')
        with self.store.db() as d:
            self._row(d,a,rid)
            rows=d.execute('SELECT * FROM events WHERE request_id=? AND sequence>? ORDER BY sequence LIMIT 128',(rid,after)).fetchall()
            return [{'event_id':rid+':'+str(r['sequence']),'request_id':rid,'sequence':r['sequence'],'timestamp':r['timestamp'],'type':r['type'],'data':json.loads(r['data'])} for r in rows]
