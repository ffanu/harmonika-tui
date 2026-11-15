"""Immutable synthetic identity provider for the owned test application.

Real infrastructure credentials are never used. Callers receive an owner alias,
not a password, token or session cookie. This is an exam adapter, not production IAM.
"""
import hmac

def authenticate(payload):
    if not isinstance(payload, dict):
        return None
    owner = payload.get('username')
    provided = payload.get('password')
    if owner not in ('alpha', 'beta') or not isinstance(provided, str):
        return None
    expected = 'demo-' + owner
    return owner if hmac.compare_digest(provided, expected) else None
