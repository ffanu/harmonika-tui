#!/usr/bin/env python3
"""On-host scheduler: managed Zimbra certificate plus existing other lineages.

The other certificates retain issuance but never deploy the primary Zimbra cert
through the legacy unscoped directory hook. No key/account/output is returned.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PRIMARY = 'mail.coredigital.net.id'
WORKER = '/usr/local/lib/harmonika/zimbra_ssl_worker.py'
CONFIG = '/etc/harmonika/zimbra-ssl/' + PRIMARY + '.json'
LOCK = '/var/lib/harmonika/zimbra-ssl/maintenance.lock'


def other_lineages(directory, primary=PRIMARY):
    result=[]
    for p in sorted(directory.glob('*.conf')):
        if p.is_symlink() or not p.is_file() or p.stat().st_uid != 0 or p.stat().st_mode & 0o022:
            raise ValueError('unsafe_renewal_config')
        label=p.stem
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,126}[a-z0-9]',label) or '..' in label:
            raise ValueError('invalid_lineage')
        if label != primary:result.append(label)
    if len(result)>32:raise ValueError('lineage_limit')
    return result


def argv(label):
    # Certbot itself decides due status; pre/post hooks run only for an attempt.
    return ['/usr/bin/certbot','renew','--cert-name',label,'--non-interactive','--no-directory-hooks',
            '--no-random-sleep-on-renew','--pre-hook',"/usr/bin/su - zimbra -c '/opt/zimbra/bin/zmproxyctl stop'",
            '--post-hook',"/usr/bin/su - zimbra -c '/opt/zimbra/bin/zmproxyctl start'",'--deploy-hook','/bin/true']


def main():
    if os.geteuid()!=0:return 2
    # The managed worker performs its own global lock and persistent recovery.
    p=subprocess.run(['/usr/bin/python3','-I',WORKER,'--config',CONFIG,'--renew'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if p.returncode:return p.returncode
    marker=Path('/var/lib/harmonika/zimbra-ssl')/PRIMARY/'reconciliation-required.json'
    if marker.exists():return 3
    fd=os.open(LOCK,os.O_RDWR|os.O_NOFOLLOW)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for label in other_lineages(Path('/etc/letsencrypt/renewal')):
            # Bound the whole systemd service rather than killing an ACME child
            # in the middle of proxy maintenance; Certbot owns post-hook cleanup.
            p=subprocess.run(argv(label),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if p.returncode:return p.returncode
    finally:os.close(fd)
    return 0


if __name__=='__main__':raise SystemExit(main())
