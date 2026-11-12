"""Fixed preflight; inlined after reviewed zimbra.renewal source by the adapter."""
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys


def read_config(path):
    if path.is_symlink():raise ValueError('unsafe_renewal_config')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode)or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('unsafe_renewal_config')
    with path.open()as handle:text=handle.read(65537)
    return selected_config(text)


def preflight(cert_name):
    cert_name=fqdn(cert_name);config=read_config(Path('/etc/letsencrypt/renewal')/(cert_name+'.conf'))
    cli=Path('/etc/letsencrypt/cli.ini')
    global_config=read_config(cli)if cli.exists()else{'hooks':{}}
    hooks=[]
    for phase in ('pre','post','deploy'):
        directory=Path('/etc/letsencrypt/renewal-hooks')/phase
        if not directory.is_dir()or directory.is_symlink():continue
        for path in list(sorted(directory.iterdir()))[:32]:
            if not path.is_file()or path.is_symlink():continue
            metadata=path.stat()
            if metadata.st_size>65536:continue
            text=path.read_text(errors='replace')
            hooks.append({'phase':phase,'name':path.name,'sha256':hashlib.sha256(text.encode()).hexdigest(),
                          'safe_owner_mode':metadata.st_uid==0 and not bool(metadata.st_mode&0o022),
                          'stops_proxy':bool(re.search(r'zmproxyctl\s+stop',text)),
                          'restarts_zimbra':bool(re.search(r'zmcontrol\s+restart',text)),
                          'uses_lineage_env':'RENEWED_LINEAGE'in text})
    try:
        with socket.create_connection(('127.0.0.1',80),2):busy=True
    except OSError:busy=False
    help_result=subprocess.run(['/usr/bin/certbot','--help','all'],capture_output=True,text=True,timeout=8)
    cert=Path('/etc/letsencrypt/live')/cert_name/'cert.pem'
    validity=subprocess.run(['/usr/bin/openssl','x509','-in',str(cert),'-noout','-checkend','604800'],capture_output=True,timeout=5)
    config_hooks=any(v['configured']for c in (config,global_config)for v in c.get('hooks',{}).values())
    proof=subprocess.run(['/usr/bin/python3','-I','-c',HOOK_PROOF],capture_output=True,timeout=10)
    override_verified=proof.returncode==0 and proof.stdout.strip()==b'1'
    inventory={'authenticator':config['fields'].get('authenticator'),'port80_listening':busy,
               'no_directory_hooks_supported':'--no-directory-hooks'in help_result.stdout,
               'cli_hook_override_verified':override_verified,'source_cert_valid':validity.returncode==0,
               'configured_command_hooks_present':config_hooks,'directory_hooks':hooks,
               'selected_config':config}
    return {'ok':True,'cert_name':cert_name,'inventory':inventory}


if __name__=='__main__':
    try:print(json.dumps(preflight(sys.argv[1])))
    except Exception as exc:print(json.dumps({'ok':False,'error_type':type(exc).__name__,'error':'renewal_preflight_failed'}))
