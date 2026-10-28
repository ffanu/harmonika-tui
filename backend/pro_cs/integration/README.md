# Central conversation preflight integration

This patch connects the actual Node `/v1/chat/completions` handler to the already
active Pro CS engine at loopback18321. The Go gateway source and active agent CS
sources were inspected: requests routed through the public, member/web,
customer-agent and internal-agent (TUI tool-calling) paths reach this handler.
Website CS classification calls also use it. On 2026-10-28 the actual Inbox/PPP preview worker was migrated to a native
Harmonika-only provider through the authenticated private CS adapter on port18328.
Direct legacy providers and local OpenCode drafting are disabled for this path.
See `/Users/id/Projects/harmonika-cs-provider/ACTIVATION-REPORT.json`. Read-only agent inspection did not modify Inbox/PPP/Website Chat.

The bridge invokes `context.build.smart` on real request context through the
private service secret. It is explicitly **advisory preflight**: not scoped
stateful delivery authorization, a new post-generation validator, independent
fact verification, or an activation of the entire243-skill catalog. Existing
preview, evidence/SOP publication, lease/sequence, takeover and closing checks
remain with their current channel owners. No new sending or financial action.

Ordinary supported conversations get a static safety instruction independent of
engine availability. Internal coding bodies, tools/tool history, strict JSON
system prompts, response schemas and website-classifier prompts are preserved.
The existing12000-character Google bridge budget is preserved; an instruction
that cannot fit is omitted, without truncating caller input. Partial tool history
is not interpreted as a complete conversation. Oversized context is reported as
preflight degraded while the original model receives its original input.

All network calls use a fixed loopback URL, no redirects, bounded output and a
700ms deadline. Three failed calls open a10-second circuit. Logs include only
allowlisted client/status/reason/latency fields, no prompts, IDs, content hashes,
source text, headers or secrets. Internal tool requests must pass secret checking
before they can invoke the advisory service. Public client labels are attribution,
not a new authentication/privilege boundary.

## Activated release

PC1 stage: `/opt/harmonika-ai/pro-cs-engine-stage`.
`activate.py` is dry-run unless BOTH `--apply --restart-main-api-approved`.
It checks original source SHA and release SHA, syntax-checks both JS files,
waits for two idle samples with no inbound requests/child processes, creates a
source-only rollback, and restarts only `harmonika-ai.service`. Failed health
checks restore source and restart the same service. It never signals an AI
terminal/session or restarts agent services. Do not apply without the explicit
user exception to the global AGENTS restriction on existing processes.

Tests:12 bridge unit tests and12 isolated handler cases (six client types,
streaming/non-streaming). Handler tests call the real Pro CS engine with synthetic
messages and a fake provider, not real customer conversations or model inference.
OpenCode reviewed the patch; UTF-8 parsing, patcher assertions, contract protection
and a circuit breaker were addressed. On 2026-10-28 the user explicitly approved and completed the main API restart
(PID1487202 to2226199); health returned HTTP200. Production metadata confirms
all six preflight client types connected. Synthetic handler checks verified tool
selection and attachment rejection. A real isolated TUI model decision returned
a valid tool call; no function was executed. These checks do not establish
end-to-end customer response quality by themselves. The separate native-provider migration report records the
subsequent actual Inbox/PPP worker cutover and synthetic inference tests.

Other endpoints such as direct `/ask` administrative commands and the direct
Meta proxy are not intercepted. Automatic Meta routing reached from the standard
chat handler is covered. The hook does not automatically invoke all file readers,
replace CS retrieval, or connect every external model/CRM adapter.
