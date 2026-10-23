#!/usr/bin/python3
"""Validate a user's own Harmonika key; never grants or fabricates entitlements."""
import getpass
import json
import os
from pathlib import Path
import re
import sys
from urllib import request,error
from uuid import uuid4

ENDPOINT='https://chat.harmonika.id/v1/public-ai/capabilities'
CONFIG=Path.home()/'.config/harmonika'

class LicenseError(ValueError):pass
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise LicenseError('license_redirect_refused')

def validate_key(key):
    if not isinstance(key,str) or not re.fullmatch(r'hai_[A-Za-z0-9_]{16,240}',key):raise LicenseError('invalid_key_format')
    return key

def summary(data):
    if not isinstance(data,dict) or data.get('ok') is not True:raise LicenseError('invalid_license_response')
    features=data.get('features');limits=data.get('limits')
    if not isinstance(features,dict) or not isinstance(limits,dict):raise LicenseError('invalid_license_response')
    integer=lambda field:limits[field] if type(limits.get(field)) is int and limits[field]>=0 else None
    return {'ok':True,'provider':'Harmonika Ai','valid':True,'chat':features.get('chat') is True,
        'coding':features.get('function_calling') is True and features.get('tool_calls') is True,
        'unlimited':limits.get('unlimited') is True,'requests_per_day':integer('requests_per_day'),
        'max_message_bytes':integer('max_message_bytes')}

def check(key,opener=None):
    validate_key(key)
    opener=opener or request.build_opener(NoRedirect())
    req=request.Request(ENDPOINT,headers={'Authorization':'Bearer '+key,'Accept':'application/json'})
    try:
        with opener.open(req,timeout=20) as response:
            body=response.read(65537)
            if len(body)>65536:raise LicenseError('license_response_too_large')
            return summary(json.loads(body))
    except error.HTTPError as failure:
        if failure.code in (401,403):raise LicenseError('invalid_or_revoked_license') from None
        if failure.code==429:raise LicenseError('license_rate_limited') from None
        raise LicenseError('license_service_unavailable') from None
    except (error.URLError,TimeoutError,OSError,json.JSONDecodeError):raise LicenseError('license_service_unavailable') from None

def activate(key,config=CONFIG,checker=check):
    result=checker(validate_key(key))
    if not result.get('coding'):raise LicenseError('key_not_enabled_for_terminal_coding')
    config=Path(config);config.mkdir(parents=True,exist_ok=True,mode=0o700)
    if config.is_symlink():raise LicenseError('unsafe_config_directory')
    target=config/'harmonika-api-key'
    if target.is_symlink():raise LicenseError('unsafe_key_file')
    pending=config/('.key-'+uuid4().hex)
    try:
        descriptor=os.open(pending,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'w') as stream:stream.write(key+'\n')
        os.replace(pending,target)
    finally:
        if pending.exists():pending.unlink()
    return {**result,'saved':True}

def main(args):
    if args==['check']:
        file=CONFIG/'harmonika-api-key'
        if not file.is_file() or file.is_symlink():raise LicenseError('license_key_not_configured')
        result=check(file.read_text().strip())
    elif args==['activate']:
        if not sys.stdin.isatty():raise LicenseError('activation_requires_private_terminal_prompt')
        result=activate(getpass.getpass('Key Harmonika: ').strip())
    else:raise LicenseError('usage: python3 runtime/license.py check|activate')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main(sys.argv[1:])
    except LicenseError as failure:
        print(json.dumps({'ok':False,'error':str(failure)}),file=sys.stderr);sys.exit(1)
