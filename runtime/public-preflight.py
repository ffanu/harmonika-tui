#!/usr/bin/python3
"""Fail closed: a working owner installation is not proof of public readiness."""
import json
from pathlib import Path
import re
import sys

SOURCE=Path(__file__).resolve().parent.parent

def audit(source=SOURCE):
    blockers=[]
    files=[p for p in (source/'runtime').rglob('*') if p.is_file() and p.suffix in {'.py','.cjs','.json'}]
    personal=[];bridge=[]
    for file in files:
        if '__pycache__' in file.parts or file.name in {'public-preflight.py'}:continue
        text=file.read_text(errors='replace')
        if '/Users/id/' in text:personal.append(str(file.relative_to(source)))
        if re.search(r'root@[0-9]{1,3}(?:\.[0-9]{1,3}){3}',text):bridge.append(str(file.relative_to(source)))
    if personal:blockers.append({'code':'owner_paths_not_portable','files':personal})
    if bridge:blockers.append({'code':'owner_ssh_transport_not_public','files':bridge})
    blockers.append({'code':'public_media_transport_unavailable','detail':'Owner SSH is disabled by default; a licensed HTTPS media transport is still required for public clients.'})
    blockers.append({'code':'public_binary_bootstrap_unverified','detail':'Runtime archives omit the binary; fresh-machine bootstrap has not been certified.'})
    blockers.append({'code':'licensed_coding_tier_required','detail':'Telegram public token defaults currently lack coding and unlimited entitlements; do not distribute an owner internal key.'})
    return {'ok':not blockers,'public_ready':not blockers,'provider':'Harmonika Ai','blockers':blockers,
        'next':['Per-user revocable licensed keys and server-side entitlements','Public HTTPS tool bridge; no SSH credentials on clients','Pinned platform artifacts and fresh-HOME install tests','Allowlisted export and upstream license attribution review']}

if __name__=='__main__':
    result=audit();print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(0 if result['public_ready'] else 2)
