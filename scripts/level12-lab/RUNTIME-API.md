# Protected reusable HTTP runtime (Codex-provided, assisted Level12)
After three observed source-policy failures, Codex supplied web_runtime.py as an
immutable engine component. It is NOT candidate-written code. This changes the
assessment to building the domain/application/UI on protected auth/HTTP primitives.
The native failures and assistance stay in the final report. Firewall stays active.

Integrate only this public API in app.py:

from crm import CRMStore
from web_runtime import RuntimeHandler, serve
class Handler(RuntimeHandler):
    def route(self, method, path, query, data, principal):
        store = self.server.store
        # implement business dispatch here
        # self.reply(status, JSONvalue, optional_headers)
        # self.bytes_reply(status, CSVbytes, {'Content-Type':'text/csv; charset=utf-8',
        #   'Content-Disposition':'attachment; filename="customers.csv"'})
        return self.reply(404, {'error':'not_found'})
if __name__ == '__main__':
    serve(Handler, CRMStore)

route receives a decoded path, flat query dict, validated JSON-object data for
POST/PATCH/DELETE, and a trusted principal for /api/* only. Runtime performs
login/session/logout, cookie flags, CSRF/origin, authentication, body limit/JSON,
healthz/readyz, safe error mapping, explicit static allowlist, security headers,
loopback binding/free-port fallback, initialized PID-bound atomic readiness,
8-request thread bound and mechanical startup/error logging. It uses protected
Security. Do not reimplement any authentication/cookie/HTTP transport in app.py.
Do not edit web_runtime.py or security.py. No credentials go in candidate source.

YOU implement all business routes exactly per README:
GET /api/dashboard direct counts; GET lists wrapped {customers|tasks|articles:[]};
GET singular wrapped {customer|task|article:record}; POST create new201/replay200;
PATCH revision+patch wrapped record200; DELETE revision deleted200;
CSV attachment; GET /news public {articles:store.public_articles()}.
Idempotency-Key is available via self.headers.get('Idempotency-Key').
Errors from CRMStore are converted by runtime from AppError(status,code).
Unknown fields, duplicate/invalid query fields and invalid revision must be
validated by your domain/route. Keep list response/query contracts for browser QA.
No raw error trace, empty success or fake saved data. Keep UI-MAP.json as documented.

web_runtime.py is automatically included in the approved build. Optional
http_api.py helper is also approved if used, but a compact app.py route class is
sufficient. Existing app.py partial code can be replaced only after rereading it.
Frontend must keep the pinned mockup's brand/palette/cards/sidebar/mobile menu.
All CSS and JS external, no inline event handlers/styles due strict CSP.
