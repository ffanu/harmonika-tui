# Harmonika AI — Safety & Acceptable Use Policy

This document is the single reference for what Harmonika AI (Terminal TUI, MCP tools, skills
and `hai.harmonika.id`) does, what it refuses, and how to verify the difference.

## 1. Principles

1. **Defensive by default.** Security features exist to protect the operator's own systems:
   detection rules, hardening, audits, documentation and remediation guidance.
2. **Authorization first.** Any test, crawl, probe or audit runs only against assets the user
   owns or has written permission to test.
3. **Honest output.** Findings are labeled as candidates unless evidence proves otherwise. The
   product never claims a live verification it did not perform, nor a compliance certificate
   it cannot issue.
4. **Least data.** Tools analyze supplied data and return redacted/pseudonymous results. Raw
   telemetry, credentials and customer PII are not retained by the engine.

## 2. Allowed

- Static and logic review of source code you own (`harmonika-debug-scanner`).
- Operator-registered HTTP probes of your own endpoints, without exploitation.
- Responsive and accessibility QA of your own web app (`qa_matrix`).
- Blueprint mapping of a dashboard you own, using a session you legitimately hold
  (`web_blueprint`), limited to public/authorized pages.
- Defensive fraud rules, anti-bot deployment review, privacy hardening and defense plans
  (`trust_guard`): `fraud_risk`, `captcha_audit`, `privacy_audit`, `bot_defense_plan`.
- Document generation, media processing, ERP, legal research, telco and SEO work on data the
  operator provides.

## 3. Prohibited

Harmonika AI does not build, enable or instruct on the following, regardless of framing:

- Disabling or widening security filters to allow abuse.
- CAPTCHA solving, token farming, or bypassing challenge/verification systems.
- Browser fingerprint spoofing, anti-detect browser configuration, or device-identity forgery
  intended to defeat fraud detection.
- Proxy/IP rotation, traffic laundering or any technique to look like another user/device.
- Credential attacks, credential stuffing participation, phishing, or malware.
- Accessing accounts, panels or data that the user does not own or administratively control.
- Circumventing rate limits, licensing checks, paywalls or terms of service of third parties.

A request that depends on any of the above is declined with an alternative defensive path.

## 4. Implementation guarantees

| Control | Where |
| --- | --- |
| No network access in trust engine | `runtime/tools/trust_engine.py` (`features.network_access = false`) |
| No source execution in audits | `trust_engine.py`, `backend/debug_scanner/static.py` |
| Pseudonymous identifiers only | `_ref()` hashing in `trust_engine.py` |
| SSRF guard on QA/probe targets | `qa_matrix.cjs`, `smoke.cjs`, `web_blueprint.cjs` |
| Output directory confinement | `safeOut()` blocks root, home, dot-dirs, symlinks |
| License binding | `X-Harmonika-Device-ID` single-device policy |
| Identity consistency | `sanitizeIdentity` keeps vendor claims out of answers |

## 5. Boundaries you can check

`trust_guard` with `operation: "capabilities"` returns machine-readable flags:

```json
{"features": {"fingerprint_evasion_tooling": false, "captcha_solving": false,
             "detection_circumvention": false, "defensive_rule_generation": true}}
```

Every trust result also carries `"attack_confirmed": false`, `"enforcement_performed": false`
and `"telemetry_retained": false`, plus an explicit `limitations` list.

## 6. Reporting a problem

If a tool behaves outside this policy, or you need an exception for a legitimate penetration
test of an asset you own, contact the maintainer before running it. Do not widen scope
silently.
