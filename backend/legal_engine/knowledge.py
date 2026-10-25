"""Bounded source retrieval and uncertainty-aware version checks, no invented article labels."""
import re
from .common import require
from .sources import citation
S={'type':'string','minLength':1,'maxLength':100};HANDLERS={};SCHEMAS={};MUTATIONS=set();LIMITATIONS={}
def obj(p,r=None):return {'type':'object','properties':p,'required':list(p) if r is None else r,'additionalProperties':False}
def reg(id,schema,fn,note):HANDLERS[id]=fn;SCHEMAS[id]=schema;LIMITATIONS[id]=note

def search(c,a):
 q=a['query'];rows=c.sources.search(q,jurisdiction=a.get('jurisdiction','ID'),limit=a.get('limit',5),kind='uu')
 out=[]
 words=re.findall(r'\w{3,40}',q.lower())
 for s in rows:
  positions=[s['body'].lower().find(w) for w in words];positions=[n for n in positions if n>=0];start=max(0,min(positions or [0])-80);quote=s['body'][start:start+450]
  out.append({'source_id':s['id'],'title':s['title'],'currentness':s['currentness'],'citation':citation(s,quote)})
 return {'matches':out,'retrieval':'bounded_lexical_official_corpus','comprehensive_search':False,'status':'sources_found' if out else 'source_not_found','can_infer_no_law_exists':False}
reg('kb.uu.search',obj({'query':{'type':'string','minLength':1,'maxLength':500},'jurisdiction':S,'limit':{'type':'integer','minimum':1,'maximum':10}},['query']),search,'Searches only the explicitly ingested official corpus; not every Indonesian law or a vector/semantic search.')
def get(c,a):
 s=c.sources.get(a['law_id']);return {'source':{k:v for k,v in s.items() if k!='body'},'citation':citation(s,s['body'][:450]) if s.get('has_full_text') else None,'status':'completed' if s.get('has_full_text') else 'degraded','currentness_certified':False}
reg('kb.uu.get',obj({'law_id':S}),get,'Returns a server-fetched snapshot; this does not certify all amendments, constitutional rulings or temporal applicability.')
def article(c,a):
 s=c.sources.get(a['law_id']);require(s.get('has_full_text'),'full_law_text_required',409);ref=a['article'];require(re.fullmatch(r'[0-9]{1,4}[A-Z]?',ref) is not None,'invalid_article_reference')
 matches=list(re.finditer(r'(?im)^\s*Pasal\s+'+re.escape(ref)+r'\s*$',s['body']));require(matches,'article_not_found',404)
 start=matches[0].start();following=re.search(r'(?im)^\s*Pasal\s+[0-9]+[A-Z]?\s*$',s['body'][matches[0].end():]);end=matches[0].end()+following.start() if following else len(s['body']);text=s['body'][start:min(end,start+8000)]
 return {'article_requested':ref,'text':text,'truncated':end-start>8000,'citation':citation(s,text[:450]),'currentness_certified':False,'applicability_determined':False}
reg('kb.uu.pasal.get',obj({'law_id':S,'article':S}),article,'Exact article-heading extraction from full official snapshot only; old labels in requested spec are not used as legal meanings.')
def history(c,a):
 rows=c.sources.history(a['law_id']);return {'snapshots':rows,'legal_amendment_history_complete':False,'snapshot_changes_are_not_amendments':True}
reg('kb.uu.history.track',obj({'law_id':S}),history,'Tracks fetched document snapshots, explicitly not a complete amendment/repeal graph.')
def version_check(c,a):
 s=c.sources.get(a['law_id']);patterns={'status':r'berlaku|dicabut|diubah|mencabut','replacement':r'dicabut|mencabut|mengganti','effective':r'mulai berlaku|diundangkan','transition':r'peralihan|berlaku sampai|sebelum','cross_reference':r'undang-undang nomor|putusan|peraturan pemerintah'}
 pattern=patterns[a.get('_check','status')];hits=list(re.finditer(pattern,s['body'],re.I))[:8];quotes=[]
 for hit in hits:
  start=max(0,hit.start()-80);quote=s['body'][start:start+450];quotes.append(citation(s,quote))
 return {'status':'degraded','law_id':a['law_id'],'citations':quotes or [citation(s,s['body'][:450])],'current_legal_status':'not_certified','applies_to_case':None,'needs_authorized_review':True,'reason':'amendment_repeal_judgment_transition_graph_not_complete','warning':'Kutipan dapat merujuk aturan lain. Jangan menyimpulkan status atau tanggal berlaku hanya dari kemunculan kata.'}
reg('kb.uu.replace.check',obj({'law_id':S}),lambda c,a:version_check(c,{**a,'_check':'replacement'}),'Evidence excerpts only; replacement/applicability unknown without complete authoritative relationship graph.')
for id,check in [('uu.status.check','status'),('uu.replace.find','replacement'),('uu.effective.date','effective'),('uu.transition.rule','transition'),('uu.cross_reference','cross_reference'),('uu.implementing.regulation','cross_reference')]:
 reg(id,obj({'law_id':S}),lambda c,a,key=check:version_check(c,{**a,'_check':key}),'Degraded review aid from exact source excerpts; no currentness, replacement, effective-date or implementing-rule certification.')
def multiple(c,a):
 require(len(set(a['source_ids']))==len(a['source_ids']),'duplicate_sources');rows=[c.sources.get(x) for x in a['source_ids']];require(len(rows)>=2,'independent_sources_required')
 return {'independent_authorities':sorted(set(s['authority'] for s in rows)),'independent_authority_count':len({s['authority'] for s in rows}),'citations':[citation(s,s['body'][:300]) for s in rows],'contents_identical':len({s['sha256'] for s in rows})==1,'currentness_certified':False,'agreement_or_applicability_inferred':False}
reg('uu.multiple_source.verify',obj({'source_ids':{'type':'array','minItems':2,'maxItems':5,'items':S}}),multiple,'Compares snapshot provenance/hash and distinct hosts only; agreement does not establish current legal applicability.')

# Async transport/runtime intercepts this operation before opening a case DB writer.
from .sources import MANIFEST
reg('kb.update.sync',obj({'source_id':{'type':'string','enum':sorted(MANIFEST)}}),lambda c,a:{'status':'degraded','reason':'source_sync_transport_required'},'Refreshes one allowlisted official document through the runtime outside case transactions; no all-law/currentness certification.')
