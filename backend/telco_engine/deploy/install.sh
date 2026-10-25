#!/bin/sh
# Installs ONLY the dedicated telco engine. Gateway rollout remains separate.
set -eu
[ "$(id -u)" = 0 ] || { echo 'root_required' >&2; exit 1; }
[ -f /opt/harmonika-ai/telco_engine/service.py ] || { echo 'source_missing' >&2; exit 1; }
[ -x /opt/harmonika-ai/coding-venv/bin/python ] || { echo 'runtime_missing' >&2; exit 1; }
[ -s /etc/harmonika-ai/internal-agent.token ] || { echo 'internal_engine_auth_missing' >&2; exit 1; }
install -d -m 700 /var/lib/harmonika-telco
if [ ! -s /etc/harmonika-ai/telco-engine.token ]; then
    umask 077
    /opt/harmonika-ai/coding-venv/bin/python -c 'import secrets; from pathlib import Path; p=Path("/etc/harmonika-ai/telco-engine.token"); p.write_text(secrets.token_hex(32));p.chmod(0o600)'
fi
chmod 600 /etc/harmonika-ai/telco-engine.token
install -m 644 /opt/harmonika-ai/telco_engine/deploy/harmonika-telco-engine.service /etc/systemd/system/harmonika-telco-engine.service
systemctl daemon-reload
systemctl enable harmonika-telco-engine.service
systemctl restart harmonika-telco-engine.service
curl --fail --silent --retry 6 --retry-connrefused --retry-delay 1 --max-time 3 http://127.0.0.1:18189/healthz
