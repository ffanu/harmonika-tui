# Harmonika Pro Conversation / Customer Service Core

A generic shared draft-planning and validation engine for `mode=general` and
`mode=cs`. Installed on chat.harmonika.id; independent from existing customer
channel applications. Codex implemented and verified it; OpenCode reviewed the
specification and isolation/guardrail hazards. Source guide: 243 unique skill IDs,
18 layers. The guide's 215 heading is inconsistent with its actual list.

## Runtime and interfaces

`harmonika-pro-cs.service`, loopback18321, CPU-only, private device namespace,
1GB memory cap, bounded upload workers and private temporary files. Secret-authenticated
POST endpoints: `/capabilities`, `/invoke`, `/plan`, `/validate`, `/takeover`, `/close`.
The trusted owner principal comes from the signed gateway, never request JSON.
Customer/account/conversation IDs must be supplied by authenticated channel adapters,
not treated as verified simply because a user types an ID.

Existing `/v1/terminal/rpc` accepts these signed owner operations:

- `pro_cs_capabilities` (no input fields)
- `pro_cs_invoke` with `input: {skill_id, input}`
- `pro_cs_plan` with `input: {conversation_id, customer_id, issue_id, context}`
- `pro_cs_validate` with those identity/context fields plus `draft`, `draft_revision`, `source_ids`
- `pro_cs_takeover` / `pro_cs_close` with identity fields and boolean `enabled`

IDs are distinct: one customer can have multiple accounts/conversations/issues.
Context includes `messages` (user/assistant/cs roles), integer `revision`, optional
`evidence`, `media`, `mode` and `channel`. Evidence items have `id`, `text`, optional
`topic` and `expires_at`. Hints are deterministic, not calibrated confidence scores.

Every customer burst advances revision. Equal revisions with changed content
conflict; old drafts cannot validate after a newer revision. Takeover and closing
suppress replies. Release/reopen must be explicit. Consumers must validate again
immediately before operator review/delivery; this core **does not send** messages.
All responses have `auto_send=false`; there is no refund, invoice, ticket,
notification, service activation or suspension side effect.

## Public skill gateway

Six sandboxed, stateless functions are dynamically discovered without restarting
the existing registry: `pro_cs.catalog`, `pro_cs.analyze`, `pro_cs.validate`,
`pro_cs.intent`, `pro_cs.privacy`, `pro_cs.extract`.
Use the established `/v1/skills/invoke` envelope `{skill_id,input}` with API auth.
Stateless analysis is not the persistent revision/takeover contract above.

The full 243-entry catalog has per-ID readiness. Currently 56 bounded operations
are implemented in the shared core/reader dispatcher. The remaining187 require
models, connectors, consent workflows, trained classifiers or channel-specific
integration. The catalog is not a claim that243 independent tools are operational.

## Evidence and privacy rules

- Today/recent-message context, bounded byte budgets, latest question first;
  short location follow-ups retain the preceding package question.
- Safe greeting/closing templates and extractive drafts from matching evidence.
  No fabricated package prices, dates, payment verification or technician ETA.
- Caller-provided evidence is **not independently verified**. Exact-source
  matching is a conservative validator, not proof that a source is truthful.
  Novel paraphrases require review; citations do not automatically validate claims.
- Pending/failed/empty media analysis blocks the reply. OCR text is explicitly
  distinguished from actual visual understanding.
- SQLite WAL stores HMAC scope/digest, revisions, takeover, closed and expiry only.
  No raw messages, files, identities, financial details or customer prompts are
  stored in technical logs/SQLite. Temporary parser data is deleted on completion.
- No cross-session content memory, interaction training, automatic KB learning or
  personal profile inference. Those require an explicit consent/retention adapter.

## Files and limitations

Private file input: `{name,data_base64}`; max8MiB, never arbitrary host paths or
remote URLs. Separate CPU/time/memory-limited parser process. 19 synthetic formats
were verified: PDF/DOCX/XLSX/PPTX, PNG/JPG/TIFF OCR, TXT/JSON/CSV/XML/SVG/YAML,
HTML/Markdown, ICS/VCF/EML and ZIP (ICS/VCF are text reads, not full semantic parsers).
Archives are inspected without extraction; expansion/member/path/symlink/ratio
limits apply. Skipped members produce partial/degraded results.

Scanned PDF OCR, legacy DOC/MOBI/RAW/DICOM, RAR/7z, full visual/video understanding,
ASR and signature/fraud verification are not claimed by this new dispatcher.
The legacy/RTX media adapters remain separate until properly connected. A100MB
streaming upload endpoint is not implemented; existing gateway limits are unchanged.
No antivirus is invoked and no authenticity/payment/medical verification is claimed.

Response streaming remains with the existing Harmonika model gateway. This
installation does not automatically intercept all conversations or replace the
live agent.banyu.live engine. Channel integration requires its authenticated
history/evidence and delivery checks; existing CS preview rules remain unchanged.
Target latency, CSAT and hallucination-rate numbers in the guide are goals, not
measured end-to-end guarantees or automatic-send authorization.
