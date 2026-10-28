"""Mechanical error taxonomy. No prompt, identity, exception text or endpoint data."""
import time
CODES={
 'rtx_offline':'graphics_offline','worker_busy':'graphics_busy','worker_vram_full':'graphics_vram_full',
 'rtx_timeout_or_connection_lost':'graphics_timeout','rtx_job_timeout':'graphics_timeout',
 'invalid_worker_response':'graphics_bad_payload','invalid_image_result':'graphics_bad_payload',
 'worker_response_too_large':'graphics_bad_payload','invalid_worker_result':'graphics_bad_payload',
 'corrupt_image':'graphics_bad_payload','unsupported_image':'graphics_bad_payload','file_too_large':'graphics_bad_payload','invalid_artifact':'graphics_bad_payload','artifact_checksum_mismatch':'graphics_bad_payload',
 'worker_auth_failed':'graphics_auth_failed','worker_warming':'graphics_warming',
 'gpu_job_recovery_required':'graphics_recovery_required','remote_job_still_pending':'graphics_recovery_required',
 'vision_unavailable':'graphics_warming','generation_unavailable':'graphics_warming',
}
STATES=frozenset({'online','offline','busy','vram_full','warming','timeout','bad_payload','auth_failed','recovery_required','unavailable','unknown'})
STATE_CODES={'offline':'rtx_offline','busy':'worker_busy','vram_full':'worker_vram_full','warming':'worker_warming','timeout':'rtx_timeout_or_connection_lost','bad_payload':'invalid_worker_response','auth_failed':'worker_auth_failed','recovery_required':'gpu_job_recovery_required','unavailable':'rtx_offline'}
def error_type(code):return CODES.get(code,'graphics_failed')
def observed(status,latency=0):
 if not isinstance(status,str) or status not in STATES:status='unknown'
 return {'status':status,'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'latency_ms':round(max(0,min(float(latency),600000)),3)}
def health_state(value):
 if type(value.get('ok')) is not bool or type(value.get('cuda')) is not bool or value.get('device') not in ('cuda','cpu'):return 'bad_payload'
 if value.get('recovery_required') is True:return 'recovery_required'
 if value.get('memory_full') is True:return 'vram_full'
 if value.get('busy') is True:return 'busy'
 if value.get('ok') is True and value.get('cuda') is True and value.get('device')=='cuda':return 'online'
 if value.get('cuda') is True and (value.get('approved') is False or value.get('loaded') is False):return 'warming'
 return 'unavailable'
