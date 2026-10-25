# Automatic shared-skill orchestration

Authenticated chat requests use the same Telegram-issued API key. No `skill_id`
parameter is required: a model selects declared backend tools and composes an
answer from the actual results. Explicit client tools retain the existing protocol.

The server allowlist contains twelve read-only SEO operations and four read-only engineering workflows. Visual
artifacts and drift snapshots remain local-only until private artifact delivery is
connected. Google account adapters remain unavailable without scoped authorization.
The chat API does not gain shell access, client filesystem access, or private
browser/account access.

Deployment target: chat.harmonika.id public Go gateway. `public_ai_auto_skills.go`
is compiled beside its existing package main. The private Python runtime contains
only reviewed SEO modules. `public_skill_worker.py` validates the independent
server allowlist before dispatch; no arbitrary worker path or principal is accepted
from the public request body. Provider secrets are removed from its environment.

Selection is automatic for SEO and engineering requests, including relevant recent conversation
context. A bounded lexical relevance gate avoids invoking a second planning model
for ordinary chat; the model determines actual tool calls and missing-input
clarification. This gate is a cost-control prefilter; the model chooses the actual workflow. It is not a general-purpose semantic router for every skill. Engineering evidence is bound independently from user messages, never invented tool arguments. All automatic conversation contents are redacted before the planning model is called.

Limits: one active request per token, two globally, up to three tool executions,
150-second overall deadline, 70 seconds per worker, bounded stdout. Tool output is
untrusted evidence, never instructions. A failed automatic audit returns an explicit
error; it must not silently fall back to an invented audit. SSE uses keepalive
comments while planning/executing; final text is currently emitted after synthesis.

Production activation verified on 2026-10-24. See [verification report](../../docs/AUTO-SKILLS-REPORT.json). Only the public panel gateway was restarted; core inference and other domains were untouched. Tests use a short-lived isolated public token and remove only their own request/key rows.

Engineering phase 1 adds code review, debug, testing strategy and documentation from supplied evidence. Private source-control/CI/monitoring connectors and arbitrary code execution remain disabled. Structured engineering results are returned in harmonika.reports (terminal chunk for SSE). Source/runtime release 2026.10.24.19; see [engineering contract](../../docs/ENGINEERING.md).
