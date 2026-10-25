"""Exact source index. A descriptor is not a running implementation."""
import copy,json,re
from pathlib import Path
ROOT=Path(__file__).parent
SOURCE=json.loads((ROOT/'source_index.json').read_text())
PRIMARY=frozenset(SOURCE['ids'])
EXISTING=json.loads((ROOT/'existing_catalog.json').read_text())['skills']
ROUTES={x['id']:x.get('worker') for x in EXISTING}
CORE_PATH=ROOT/'core_index.json'
CORE={r['id']:r for r in json.loads(CORE_PATH.read_text())} if CORE_PATH.exists() else {}
UNIFIED_PATH=ROOT/'unified_index.json'
UNIFIED={r['id']:r for r in json.loads(UNIFIED_PATH.read_text())} if UNIFIED_PATH.exists() else {}
AMBIGUOUS_IDS={'evidence.chain.custody','evidence.store.secure','debug.attach','debug.breakpoint','test.run','test.single','test.coverage'}
POLICY=json.loads((ROOT/'catalog_source.json').read_text())['restricted_policy']['all_restricted_ids']
# These are never activated as public autonomous attack/bypass operations.
RESTRICTED_PREFIXES=('exploit.','post.','inject.','stealth.','net.arp.','net.dns.','net.mitm','net.proxy.pivot','net.vlan.','net.wifi.crack','net.wifi.handshake')
RESTRICTED_IDS=frozenset(('account.mfa.bypass','account.password.spray','account.session.hijack','account.credential_stuff','account.brute_force','account.recovery.abuse','smart.anti_bot_bypass','workflow.captcha_solve','crypto.jwt.crack','crypto.hash.crack','crypto.rsa.attack','post.cover.tracks'))
def restricted(id):return id in POLICY or id in RESTRICTED_IDS or id.startswith(RESTRICTED_PREFIXES)
def descriptors(handlers=None):
 handlers=handlers or {};existing={x['id']:x for x in EXISTING};out=[]
 for id in sorted(set(PRIMARY)|set(existing)|set(handlers)|set(CORE)|set(UNIFIED)):
  item={'id':id,'in_master':id in PRIMARY,'execution_target':'not_implemented','status':'unavailable','reason':'implementation_missing'}
  if id in existing:
   d=existing[id];worker=d.get('worker');item.update(execution_target=d.get('execution_target'),status='context_required',reason='use_registered_engine_context',engine=worker,input_schema=copy.deepcopy(d['input_schema']),directly_invocable=worker in ('builder','coding','telco','legal'))
   if worker in ('builder','coding','legal','telco'):item['runtime_readiness_endpoint']='/v1/'+worker+'/capabilities'
   if id in AMBIGUOUS_IDS:item.update(status='domain_ambiguous',reason='explicit_engine_confirmation_required',master_meaning_implemented=False)
   if worker not in ('builder','coding','telco','legal'):item.update(status='local_only',reason='local_runtime_required',directly_invocable=False)
  if id in CORE:
   core=copy.deepcopy(CORE[id])
   if core['status'] in ('available','native_endpoint'):
    core.update(status='context_required',reason='use_core_gateway',runtime_readiness_endpoint='/v1/core/capabilities')
   item.update(core)
  if id in UNIFIED and id not in CORE:
   item.update(copy.deepcopy(UNIFIED[id]))
   if item['status']=='available':item.update(status='context_required',reason='use_unified_gateway')
   elif item['status']=='native_endpoint':item.update(status='context_required',reason='use_authenticated_native_transport',directly_invocable=False)
   elif item['status']=='internal_only':item.update(status='context_required',reason='internal_boundary_only',directly_invocable=False)
  if id.startswith('connector.') and id not in existing:item['reason']='connector_unconfigured'
  if id in handlers:
   h=handlers[id];item.update(status='available',reason=None,engine='analysis',execution_target='bounded_input_analysis',input_schema=copy.deepcopy(h['input_schema']),description=h['description'],directly_invocable=True)
  if restricted(id):item.update(status='restricted',reason='autonomous_attack_or_bypass_not_enabled',directly_invocable=False)
  out.append(item)
 return out
def summary(handlers=None):
 rows=descriptors(handlers);counts={s:sum(r['status']==s for r in rows) for s in ('available','context_required','local_only','unavailable','restricted','domain_ambiguous')}
 return {'schema_version':1,'declared_skills':1507,'declared_engine_total':1408,'source_occurrences':1203,'source_unique':1200,'duplicates':copy.deepcopy(SOURCE['duplicates']),'source_sha256':SOURCE['sha256'],'master_exact_registered':sum(r['id'] in PRIMARY and r['id'] in ROUTES for r in rows),'master_domain_ambiguous':len(AMBIGUOUS_IDS),'existing_registered':len(EXISTING),'catalog_size':len(rows),'readiness':counts,'features':{'consolidated_catalog':True,'defensive_input_analysis':bool(handlers),'isolated_html_browser':bool(handlers and 'workflow.browser' in handlers),'registered_browser_profiles_access':False,'autonomous_exploitation':False,'stealth_bypass':False,'live_customer_access':False},'limits':{'request_bytes':1048576,'analyzer_json_bytes':262144,'output_bytes':262144,'json_depth':20,'global_active':2,'per_actor_active':1,'analyses_per_minute':60,'analysis_timeout_seconds':15,'browser_timeout_seconds':40},'registration_is_not_runtime_readiness':True}
