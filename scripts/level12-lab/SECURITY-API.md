Security(db_path, config_path, origin) from protected security.py. Config path is
AUTH_CONFIG env, origin PUBLIC_ORIGIN env. No hardcoded passwords/config defaults.
AuthError has .status and .code (safe). Methods:
- check_origin(headers): exact Origin or403
- login(data, headers, peer='local'): (JSON {user:{username,role},csrf}, Set-Cookie)
- resolve(headers): (trustedprincipal, csrf);401 absent/invalid,403 forgedidentity
- require_mutation(headers, csrf): validates Origin+X-CSRF-Token
- logout(headers): authenticates+validates then returns clearing Set-Cookie
Headers support .get(). Native must catch AuthError and sqlite busy to safe errors.
Authentication SQLite tables share DATA_DIR/crm.sqlite with domain. Do not delete
sessions on startup. GET session returns {user:principal,csrf:csrf}. All credentials
are supplied only by protected operators/browser fixtures, never in candidate code.
