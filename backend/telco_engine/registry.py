"""Canonical 276-ID catalog. Registered never means verified live capability."""
import importlib,json
from pathlib import Path
SOURCE=json.loads(Path(__file__).with_name('catalog_source.json').read_text())
DOCUMENT_IDS=frozenset(x['id'] for x in SOURCE['skills'])
POLICY_BLOCKED={'prepaid.topup':'verified_payment_connector_required','sub.merge.duplicate':'verified_same_account_identity_and_complete_reference_migration_required'}
def loaded():
    handlers={};schemas={};mutations=set();limitations={}
    for module in ('analysis','planning','assurance','commercial','operations','extensions'):
        try:m=importlib.import_module('.'+module,__package__)
        except ModuleNotFoundError as e:
            if e.name==__package__+'.'+module:continue
            raise
        assert not set(handlers)&set(m.HANDLERS),'duplicate domain handler'
        assert set(m.HANDLERS)<=DOCUMENT_IDS,'unknown source skill'
        assert set(m.HANDLERS)==set(m.SCHEMAS),'schema coverage mismatch'
        assert set(m.MUTATIONS)<=set(m.HANDLERS),'unknown mutation'
        handlers.update(m.HANDLERS);schemas.update(m.SCHEMAS);mutations.update(m.MUTATIONS);limitations.update(m.LIMITATIONS)
    for id in POLICY_BLOCKED:handlers.pop(id,None);schemas.pop(id,None);mutations.discard(id)
    return handlers,schemas,mutations,limitations

def dependency(skill):
    if skill in POLICY_BLOCKED:return POLICY_BLOCKED[skill]
    if skill.startswith(('olt.','prov.configure.','prov.activate.','prov.suspend.','prov.unsuspend.','prov.terminate.','prov.test.','qos.speedtest','noc.monitor.')):return 'verified_network_connector_required'
    if skill.startswith(('bill.refund.','portal.payment.','prepaid.topup','bill.payment.match')):return 'verified_payment_or_billing_connector_required'
    if skill=='sub.kyc.verify':return 'authorized_identity_verification_connector_required'
    if skill.startswith(('sms.','voice.','nps.survey.','bill.invoice.send','prepaid.expire.')):return 'consented_communication_connector_required'
    if skill.startswith(('interconnect.','roaming.','numbering.')):return 'operator_protocol_and_source_connector_required'
    if skill.startswith(('reg.','report.regulatory.')):return 'authorized_policy_and_regulator_workflow_required'
    if skill.startswith(('predict.','sub.churn.')):return 'validated_model_or_evidence_required'
    return 'domain_implementation_or_verified_adapter_required'

def catalog():
    h,s,m,l=loaded();out=[]
    for row in SOURCE['skills']:
        id=row['id'];ready=id in h
        description='Calculate only an explicit effective tax policy/rate, with no statutory default or applicability claim.' if id=='bill.tax.calculate' else row['description']
        out.append({**row,'description':description,'version':'1.0.0','permissions':{'tenant_records':['read','write'] if id in m else ['read'],'network':False,'shell':False},'rate_limit':{'calls_per_minute':120},'timeout_seconds':30,'access':['api','tui'],'output_schema':{'type':'object','required':['id','skill_id','status'],'properties':{'id':{'type':'string'},'skill_id':{'type':'string'},'status':{'type':'string','enum':['approval_required','running','completed','failed','stopped','expired','interrupted']}}},'execution_target':'telco_tenant','scope':'isolated_tenant','company_data_connected':False,'network_live':False,'requires_approval':id in m,'readiness':{'status':'available' if ready else 'unavailable','reason':None if ready else dependency(id)},'input_schema':s.get(id,{'type':'object','properties':{},'additionalProperties':False}),'effect':('manual_verification_case' if id=='sub.kyc.verify' else 'draft_only' if id in ('bill.refund.process','sub.address.verify','partner.portal.access','portal.package.change','workflow.trigger','workflow.notification','fraud.blacklist.subscriber','revass.recovery.action') else 'tenant_local'),'limitations':l.get(id,'Tenant-local records/calculation only; no company/network/payment access.' if ready else 'Requires an authorized verified adapter; no live effect is implied.')})
    return out
