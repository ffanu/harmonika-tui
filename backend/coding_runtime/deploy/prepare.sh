#!/bin/sh
# Run on the authorized Harmonika engine host only. No keys printed.
set -eu
python3 -c 'import sys; assert sys.version_info >= (3,11), "Harmonika coding backend requires Python >=3.11"'
install -d -m 700 /var/lib/harmonika-coding /etc/harmonika-ai
python3 - <<'PY'
import os,secrets
p='/etc/harmonika-ai/coding-runtime.token'
if not os.path.exists(p):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32)+'\n')
PY
python3 -m venv /opt/harmonika-ai/coding-venv
/opt/harmonika-ai/coding-venv/bin/pip install -q -r /opt/harmonika-ai/coding_runtime/requirements.txt
install -m 644 /opt/harmonika-ai/coding_runtime/deploy/harmonika-coding-runtime.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now harmonika-coding-runtime.service
