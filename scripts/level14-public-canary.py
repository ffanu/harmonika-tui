#!/usr/bin/env python3
"""Read-only public authentication/capability canary; key never leaves headers."""
import importlib.util,json,urllib.request,urllib.error
from pathlib import Path
REPO=Path(__file__).resolve().parents[1];R=Path(Path('/tmp/harmonika-level14-root.path').read_text().strip())
spec=importlib.util.spec_from_file_location('license',REPO/'runtime/license.py');lic=importlib.util.module_from_spec(spec);spec.loader.exec_module(lic)

def main():
    key=(lic.CONFIG/'harmonika-api-key').read_text().strip();opener=urllib.request.build_opener(lic.NoRedirect())
    def call(path,extra=None,authorized=True):
        headers={'Accept':'application/json','X-Harmonika-Device-ID':lic.device_id()}
        if authorized:headers['Authorization']='Bearer '+key
        headers.update(extra or {})
        try:
            with opener.open(urllib.request.Request('https://chat.harmonika.id/v1/coding'+path,headers=headers),timeout=30) as res:return res.status,json.loads(res.read(200000))
        except urllib.error.HTTPError as e:return e.code,{}
    status,caps=call('/capabilities');assert status==200 and caps['ready'] and caps['features']['action_evidence'] and caps['features']['local_client_receipts_trusted'] is False
    canary=json.loads((R/'level14-live-canary.json').read_text())
    denied,_=call('/workspaces/'+canary['workspace_id'],{'X-Harmonika-Principal':canary['operator_principal'],'X-Harmonika-Coding-Secret':'caller-forged'})
    assert denied==404,'public_gateway_accepted_forged_principal_or_bridge_header'
    unauthorized,_=call('/capabilities',authorized=False);assert unauthorized in (401,403)
    report={'status':status,'ready':caps['ready'],'skills':len(caps['skills']),'features':caps['features'],'forged_principal_workspace_http':denied,'unauthenticated_http':unauthorized,'real_public_token_auth_tested':True,'customer_workspace_mutated':False,'raw_key_logged':False}
    (R/'public-capabilities-after.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
