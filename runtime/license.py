#!/usr/bin/python3
"""Validate a user's own Harmonika key; never grants or fabricates entitlements."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
from urllib import request,error
import uuid
from uuid import uuid4

ENDPOINT='https://chat.harmonika.id/v1/public-ai/capabilities'
CONFIG=Path.home()/'.config/harmonika'
OWNER_WHATSAPP='085771378237'

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

class LicenseError(ValueError):pass
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise LicenseError('license_redirect_refused')

def device_id():
    id_file=CONFIG/'device-id'
    try:
        if id_file.is_file():
            saved=id_file.read_text().strip()
            if re.fullmatch(r'dev_[a-f0-9]{32}',saved):return saved
    except Exception:pass
    raw=f"{platform.node()}|{platform.system()}|{platform.machine()}|{uuid.getnode()}"
    computed='dev_'+hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
    try:
        CONFIG.mkdir(parents=True,exist_ok=True)
        id_file.write_text(computed+'\n')
    except Exception:pass
    return computed

def device_name():
    node=platform.node() or 'terminal'
    return f"{node} ({platform.system()}-{platform.machine()})"[:72]

def clean_key_input(raw):
    if not isinstance(raw,str):return ''
    text=raw.strip()
    if '\r' in text or '\n' in text:return text
    m=re.search(r'hai_[A-Za-z0-9_]{16,240}',text)
    return m.group(0) if m else text

def validate_key(key):
    cleaned=clean_key_input(key)
    if not isinstance(cleaned,str) or not re.fullmatch(r'hai_[A-Za-z0-9_]{16,240}',cleaned):raise LicenseError('invalid_key_format')
    return cleaned

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
    req=request.Request(ENDPOINT,headers={
        'Authorization':'Bearer '+key,
        'Accept':'application/json',
        'X-Harmonika-Device-ID':device_id(),
        'X-Harmonika-Device-Name':device_name(),
    })
    try:
        with opener.open(req,timeout=20) as response:
            body=response.read(65537)
            if len(body)>65536:raise LicenseError('license_response_too_large')
            return summary(json.loads(body))
    except error.HTTPError as failure:
        if failure.code==403 and failure.fp is not None:
            try:
                err_body=json.loads(failure.read(4096).decode('utf-8',errors='ignore'))
                if err_body.get('error',{}).get('code')=='device_already_bound':
                    raise LicenseError('device_already_bound') from None
            except LicenseError:
                raise
            except Exception:
                pass
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

def interactive_tui_box(config=CONFIG):
    if not sys.stdin.isatty():
        raise LicenseError('activation_requires_private_terminal_prompt')
    orange='\033[38;2;255;123;0m'
    amber='\033[38;2;255;166;87m'
    green='\033[38;2;63;185;80m'
    red='\033[38;2;248;81;73m'
    bold='\033[1m'
    dim='\033[38;2;139;148;158m'
    fg='\033[38;2;240;246;252m'
    reset='\033[0m'
    box = f"""
{orange}  ╭──────────────────────────────────────────────────────────────────────╮{reset}
{orange}  │{reset}  {bold}{orange}█   █ ▄▀▀▀▄ █▀▀▀▄ █▀▄▀█ ▄▀▀▀▄ █▀▀▄█ ▀█▀ █  ▄▀ ▄▀▀▀▄{reset}                 {orange}│{reset}
{orange}  │{reset}  {bold}{amber}█▀▀▀█ █▀▀▀█ █▀▀▀▄ █ ▀ █ █   █ █  ▀█  █  █▀▀▄  █▀▀▀█{reset}   {bold}{fg}T E R M I N A L{reset} {orange}│{reset}
{orange}  │{reset}  {bold}{orange}▀   ▀ ▀   ▀ ▀   ▀ ▀   ▀  ▀▀▀  ▀   ▀ ▀▀▀ ▀   ▀ ▀   ▀{reset}                 {orange}│{reset}
{orange}  ├──────────────────────────────────────────────────────────────────────┤{reset}
{orange}  │{reset}  {bold}{fg}Selamat datang di Harmonika Ai Terminal (TUI)!{reset}                      {orange}│{reset}
{orange}  │{reset}  {dim}Aplikasi ini memerlukan API Key Lisensi aktif sebelum digunakan.{reset}    {orange}│{reset}
{orange}  │{reset}                                                                      {orange}│{reset}
{orange}  │{reset}  {amber}•{reset} {fg}Kebijakan Lisensi :{reset} {bold}{amber}1 API Key = 1 Perangkat (Single Device){reset}       {orange}│{reset}
{orange}  │{reset}  {amber}•{reset} {fg}Perangkat Ini     :{reset} {dim}{device_name():<44}{reset}  {orange}│{reset}
{orange}  │{reset}                                                                      {orange}│{reset}
{orange}  │{reset}  {bold}{fg}Belum memiliki API Key Lisensi?{reset}                                     {orange}│{reset}
{orange}  │{reset}  {green}📞 Hubungi WhatsApp : {bold}{OWNER_WHATSAPP}{reset} {dim}(Admin Harmonika Ai){reset}           {orange}│{reset}
{orange}  ╰──────────────────────────────────────────────────────────────────────╯{reset}
"""
    sys.stderr.write(box + "\n")
    while True:
        try:
            if os.name == 'nt':
                sys.stderr.write(f"  {orange}🔑 Paste API Key Lisensi Harmonika (hai_live_...) lalu tekan Enter:{reset}\n  > ")
                sys.stderr.flush()
                entered = sys.stdin.readline().strip()
            else:
                entered = getpass.getpass(f"  {orange}🔑 Masukkan API Key Lisensi Harmonika (hai_live_...) : {reset}").strip()
                if not entered:
                    sys.stderr.write(f"  {dim}(Jika paste tersembunyi tidak terbaca, tempel di sini lalu Enter): {reset}")
                    sys.stderr.flush()
                    entered = sys.stdin.readline().strip()
        except (KeyboardInterrupt, EOFError):
            sys.stderr.write(f"\n  {dim}Aktivasi dibatalkan.{reset}\n")
            sys.exit(1)
        entered = clean_key_input(entered)
        if not entered:
            sys.stderr.write(f"  {red}✖ API Key tidak boleh kosong. Hubungi WhatsApp {OWNER_WHATSAPP} untuk meminta API Key.{reset}\n\n")
            continue
        try:
            res = activate(entered, config=config)
            sys.stderr.write(f"\n  {green}✔ Lisensi berhasil diverifikasi & dikunci untuk perangkat ini! Membuka Harmonika TUI...{reset}\n\n")
            return res
        except LicenseError as err:
            code = str(err)
            if code == 'device_already_bound':
                sys.stderr.write(f"\n  {red}✖ Lisensi API Key ini sudah digunakan di perangkat lain (1 Key = 1 Device).{reset}\n  {amber}📞 Hubungi WhatsApp {OWNER_WHATSAPP} untuk meminta API Key baru atau reset device.{reset}\n\n")
            elif code == 'invalid_key_format':
                sys.stderr.write(f"\n  {red}✖ Format API Key tidak valid (harus diawali hai_live_...).{reset}\n  {amber}📞 Hubungi WhatsApp {OWNER_WHATSAPP} jika Anda belum memiliki API Key.{reset}\n\n")
            else:
                sys.stderr.write(f"\n  {red}✖ API Key tidak valid atau gagal diverifikasi ({code}).{reset}\n  {amber}📞 Hubungi WhatsApp {OWNER_WHATSAPP} untuk meminta API Key aktif.{reset}\n\n")

def main(args):
    if args==['check']:
        file=CONFIG/'harmonika-api-key'
        if not file.is_file() or file.is_symlink():raise LicenseError('license_key_not_configured')
        result=check(file.read_text().strip())
    elif len(args)==2 and args[0]=='activate':
        result=activate(clean_key_input(args[1]),CONFIG)
    elif args==['activate']:
        if not sys.stdin.isatty():raise LicenseError('activation_requires_private_terminal_prompt')
        result=interactive_tui_box(CONFIG)
    else:raise LicenseError('usage: python3 runtime/license.py check|activate [KEY]')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main(sys.argv[1:])
    except LicenseError as failure:
        print(json.dumps({'ok':False,'error':str(failure)}),file=sys.stderr);sys.exit(1)
