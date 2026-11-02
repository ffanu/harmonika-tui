---
name: harmonika-trust-shield
description: Defensive fraud, anti-bot and privacy auditing for apps the operator owns; detection rules, CAPTCHA deployment review, fingerprint-surface hardening and layered defense plans. No evasion tooling.
---

Use this when the user asks to protect their own login, checkout, signup or public API
against abuse, to review their CAPTCHA/anti-bot deployment, or to reduce the personal data
their web app exposes. It is a defensive engine: it never produces fingerprint spoofing,
CAPTCHA solving, proxy rotation or any technique for evading someone else's fraud detection.

Call the MCP tool `trust_guard` with `operation` plus `payload`:

- `capabilities` — list operations, honest feature flags and limits.
- `fraud_risk` — score supplied auth/checkout telemetry. Payload keys: `events` (each
  `{id, ts, kind, outcome, account, ip, asn, country, device_id, session_id, ua,
  client_hints: {sec_ch_ua, sec_ch_ua_platform}, email, card_bin, amount}`),
  `window_minutes`, `thresholds`, `vpn_asns`, `datacenter_asns`, `known_devices`,
  `disposable_email_domains`. Output is pseudonymous (`account:ref_…`) with rule IDs,
  risk score and a recommended decision (allow/monitor/challenge/block).
- `captcha_audit` — review `provider` plus `client`, `server`, `rate_limit`, `other`
  booleans. Returns findings such as missing server-side verification, missing single-use
  token handling, weak score threshold, absent rate limits or CSRF.
- `privacy_audit` — review supplied source files, cookie declarations and response
  headers. Reports fingerprint surfaces, weak cookie flags and missing CSP/HSTS/
  Permissions-Policy, each with a concrete mitigation.
- `bot_defense_plan` — layered plan (edge, identity, session, payments, observability,
  incident) for the given `surface` and `stack`, with explicit excluded shortcuts.

The CLI equivalent is `python3 -I ~/.config/harmonika-terminal/current/tools/trust_cli.py <operation>`
with a JSON object on stdin (`{"data": {...}}` is also accepted).

Rules of use:

- Only analyze telemetry, source, cookies and headers the operator supplies for their own
  system. Never request, invent or retain real credentials or customer PII.
- Findings are rule-based candidates, never proof of an attack. Do not describe a subject
  as a confirmed attacker, and never claim a live endpoint was verified.
- Recommendations are for controls the operator can deploy themselves. Do not offer to
  "test against" a third party's anti-bot system or to make traffic look like another device.
- Report rule IDs, counts and mitigations; do not paste raw identifiers back to the user.
