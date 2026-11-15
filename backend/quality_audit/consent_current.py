"""Record operator-confirmed consent for current active API keys only."""
import argparse
import json
import subprocess
from pathlib import Path
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-confirmed',action='store_true',required=True)
    args=parser.parse_args()
    query="SELECT id FROM public_ai_tokens WHERE status='active' AND revoked_at IS NULL ORDER BY id LIMIT 10001"
    rows=subprocess.check_output(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-d','harmonika_web','-Atc',query],text=True)
    ids=[int(x) for x in rows.splitlines() if x.strip()]
    if len(ids)>10000:raise ValueError('consent_scope_too_large')
    config=json.loads(Path('/etc/harmonika-ai/quality-audit/config.json').read_text())
    token=Path(config['admin_token_file']).read_text().strip()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    for token_id in ids:
        request=urllib.request.Request('http://127.0.0.1:18208/consent',data=json.dumps({'token_id':token_id,'granted':True,'basis':'operator_confirmed'}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with client.open(request,timeout=5) as response:
            if json.load(response).get('granted') is not True:raise ValueError('consent_not_recorded')
    target=Path('/var/lib/harmonika-quality-audit/installation.json')
    report=json.loads(target.read_text())
    report.pop('public_keys_with_confirmed_consent',None);report.pop('future_or_service_keys_default',None)
    report.update(current_keys_with_confirmed_consent=len(ids),future_keys_default='metadata_only',consent_basis='operator_confirmed')
    tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(report,indent=2));tmp.chmod(0o600);tmp.replace(target)
    print(json.dumps({'current_keys_with_confirmed_consent':len(ids),'raw_keys_read_or_printed':False}))


if __name__=='__main__':main()
