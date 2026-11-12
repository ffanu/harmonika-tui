---
name: harmonika-zimbra
description: Diagnose and verify Zimbra repairs using registered SSH, scoped backups, dependency checks and protocol health evidence.
---

Use network_targets to verify the selected target. Use zimbra_health before/after
repairs; ok is collection, healthy is the application verdict, neither proves
message delivery. Identify the installed Zimbra/OS version first. Commands run as
zimbra where appropriate; avoid nested login-shell quoting that expands variables
before the intended shell. Prefer separate test commands or fixed structured probes.

For an SSL automation request, use zimbra_ssl_plan(target, cert_name, hostname)
in a fresh runtime and zimbra_qa(topic="ssl_automation"). A twice-daily check is
safer than forced monthly issuance; renew only when due. ConfigObj root keys are
valid. Symlink permissions do not equal target key permissions. Never confuse
config-hook override with --no-directory-hooks: verify both on installed Certbot.
Dry-run can still stop/start services through pre/post hooks. Standalone on an
occupied port80 requires scoped SSL maintenance authorization; an old fd collector
exception does not authorize proxy or whole Zimbra controls. Do not activate
until staging, SAN preservation, production trust/SPKI and recovery checks pass.
The installed ssl_worker is a separate explicit root maintenance tool; the
zimbra_ssl_plan MCP tool remains read-only and cannot grant process permission.

For a known failure, follow `recovery_guidance` returned by zimbra_health. Use
zimbra_qa(topic="index") once to find the matching reviewed Q&A, then query that
topic. It contains system mechanics, not saved conversations or training data.
Consult [Q&A.md](Q&A.md) for the decision tree. These lessons never grant permission:
reuse existing authorization within its exact scope. If the user explicitly
approves recovery of one orphan fd collector, that approval permits the specified
official stop/start on that component after identity checks; do not ask again or
expand it to another target, all statistics, mailbox, reboot or firewall changes.

For SSL/DNS tests use zimbra_tls_audit(target, mail_domain) on the registered Linux
IP target. It is read-only and returns verified TLS and bounded DNS/timer evidence.
Read the matching Q&A topics before interpreting anomalies. SMTP STARTTLS must
actually negotiate and repeat EHLO. A closed optional465 is not a mandatory-port
failure. Verify each endpoint's certificate, not only the web one. Distinguish
client TLS capability, network timeouts and server policy; preserve failing phase.
SPF with a/mx can authorize an IP absent from literal ip4. DMARC none is not a
reason to force reject. Timer-active does not prove renewal/deploy. Do not run
certbot renew/dry-run, change DNS/firewall/HSTS, send email or read keys as a
read-only test. Compare external versus loopback before blaming the application.

Check effective access and metadata before interpreting stale/missing PID files.
Mode 000 on /opt/zimbra/log is a prerequisite failure. Read the relevant vendor
permission rule, back up metadata/ACL, then make the smallest justified reversible
correction. Do not use recursive chmod/chown or run full-tree zmfixperms as a shortcut.
Bound research: state what exact original mode is unknown and justify minimal
access if vendor metadata cannot establish it; do not endlessly repeat package
lookups instead of making an authorized, reversible repair.

Use network_config for approved plan-bound mutations with explicit dry_run:false
and meaningful checks, after a private scoped backup exists. User authorization
to repair persists; do not ask again for routine actions within that scope. Start
only missing components via official controls after rechecking prerequisites. Do
not stop healthy components or use kill/pkill/reboot/reinstall/firewall/SSH changes.
Track long starts as jobs and inspect receipts/status before retrying anything.

Do not read mailbox contents, account lists, private keys, zmlocalconfig -s, full
secret-bearing configs or raw mail/auth logs. Return only technical signatures and
timestamps if log investigation is needed. Keep credentials outside prompts/argv,
Git, public reports, shared knowledge and mechanics logs.

Verify actual component states, backend listeners, HTTP/TLS and SMTP/IMAP. Separate
historic systemd failed flags from current application state. Do not silently
clear errors as a repair or claim delivery from a handshake. Record unresolved
external delivery or persistence checks honestly. Consult current Zimbra v10
administrator documentation and the installed scripts; archived pages can differ.

Check the real web route: a root-mounted login page can work while `/zimbra/`
returns 404. Use bounded unauthenticated GET, verified TLS and login-page evidence;
never change a working deployment just to satisfy an assumed URL. For controllers,
use zimbra_component status/start in a fresh runtime with this tool registered.
Start requires existing authorized repair scope and authorized_repair:true. It
skips running components; printed failure overrides exit zero. Login environment
is necessary for Perl libraries. Do not guess missing packages from a nonlogin
shell failure. A stale wrapper PID must be compared with actual collector process
identity and current sample timestamps, never removed based on age alone.

Official service controllers often fork a daemon and maintain a PID/status receipt
themselves. Inspect that behavior and use the official status check; do not
repeatedly search for a generic background scheduler when the existing controller
already provides the required tracking. interactive.jobs_register is UI metadata,
not proof that a remote command executed. If a newly installed tool is absent from
the current MCP discovery, use existing narrow SSH checks or a fresh isolated
session after reload; never stall on repeated identical tool searches.

Mailjet: diagnose the account before a key-generation loop. Use the owner-local
mailjet_credentials audit_account on the selected app.mailjet.com tab; inspect
verification flags, role, API permission, quota evidence and account notices.
requiresEmailVerification=true is an observed prerequisite, not proof that the
account email is globally unverified or that a backend request was rejected.
A free plan/key limit alone proves neither API denial nor exhausted quota.
Use zimbra_mailjet_plan for the registered server's non-secret SMTP/map preflight.
Key generation needs a separate scoped owner grant; do not repeat unknown resets.
Capture keys straight to private 0600 references; no DOM dump/screenshots/clipboard
or credentials in Q&A, logs, model arguments or command lines. Use the installed
Zimbra10 attributes and exact [host]:port LMDB key; preserve inbound restrictions.
SMTP AUTH succeeds independently from sender verification or message delivery.

For a newly created account, consult zimbra_qa(topic="mailjet_provisioning").
Observe the current path: onboarding does not prove completed activation. Bind
the selected account to its own private references and grants; do not reuse the
previous account's key. Match the selected Zimbra hostname, DNS and registered
SSH target before any relay mutation. An adjacent mail tab can belong to a
different server from an earlier exam. Browser JavaScript permission, macOS
automation permission and script errors are distinct prerequisites; report the
exact adapter error and resume only after that prerequisite changes.
The default mailbox-content restriction above has a narrow user-authorized
exception: a specifically approved Mailjet verification message may be processed
by a scoped private adapter. Never return its OTP, activation token, body or other
mailbox messages to the model. Verify the official link origin; no cookies/CDP,
invented business identity, paid subscription or unrelated mail operations.

If Apple Events JavaScript remains denied even with the menu checked, the Mac
owner adapter provides mailjet_native for native accessibility observation on
/onboarding and /account/activation only. Status returns fixed flags and required
input names; no raw text/field values. Authorized open_activation or
open_business_verification only opens the observed status/form, never submits it.
Verify after navigation and collect missing business facts from the owner. Email
verified does not mean business approved. The verification inbox host is not
necessarily the intended SMTP relay host; confirm an ambiguous target separately.

Native status also reads fixed activation-gate flags on /account/apikeys, without
returning credentials or enabling API-page actions. A submitted business ticket
and under-review notice do not authorize sending. If Account activation is
required is observed, stop key-reveal/generation attempts until provider approval.
Reuse the user's existing onboarding authorization for routine verified facts;
do not invent usage numbers, list provenance or opt-in evidence. Native AX wrappers
can duplicate an actual input. Set the true input's AXValue, verify with a fresh
observation, and require a provider receipt before calling a submission complete.
