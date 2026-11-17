"""Aggregate license mechanics. No token hashes, key material or owner details."""
from .common import require,fields
from .database import postgres

def usage(config,principal,payload,admin=False):
 fields(payload,{'scope','days'})
 scope=payload.get('scope','self');days=payload.get('days',7)
 require(scope in ('self','all'),'invalid_scope');require(scope!='all' or admin,'admin_required',403)
 require(type(days) is int and 1<=days<=90,'invalid_window')
 source=config.get('license_source');require(type(source) is dict,'license_source_unavailable',503)
 token_id=int(principal.split(':')[1]);where='TRUE' if scope=='all' else 'id='+str(token_id)
 sql="""WITH scoped AS (SELECT id,status,revoked_at,last_used_at FROM public_ai_tokens WHERE %s)
SELECT json_build_object('issued',count(*),'active',count(*) FILTER(WHERE status='active' AND revoked_at IS NULL),'ever_used',count(*) FILTER(WHERE status='active' AND revoked_at IS NULL AND last_used_at IS NOT NULL),'used_24h',count(*) FILTER(WHERE status='active' AND revoked_at IS NULL AND last_used_at>=now()-interval '24 hours'),'used_window',count(*) FILTER(WHERE status='active' AND revoked_at IS NULL AND last_used_at>=now()-make_interval(days=>%d)),'inactive',count(*) FILTER(WHERE status<>'active' OR revoked_at IS NOT NULL),'bound_devices',(SELECT count(DISTINCT d.device_id) FROM public_ai_token_devices d JOIN scoped t ON t.id=d.token_id WHERE t.status='active' AND t.revoked_at IS NULL AND d.device_id<>''),'checked_at',to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')) FROM scoped;"""%(where,days)
 value=postgres(source,sql)
 require(type(value) is dict and all(type(value.get(k)) is int for k in ('issued','active','ever_used','used_24h','used_window','inactive','bound_devices')),'invalid_license_response',502)
 return {'status':'completed','scope':scope,'window_days':days,'counts':value,'count_unit':'licenses_not_people','includes_service_and_test_keys':scope=='all'}
