# Engineering workflows

Harmonika phase 1 adds four read-only operations to the shared registry:
`engineering.code_review`, `engineering.debug`, `engineering.testing_strategy`,
and `engineering.documentation`.

The public chat contract remains `POST /v1/chat/completions` with the existing
Telegram-issued Bearer token and ordinary `messages`. No skill selection or new
API key is needed. Explicit client-owned tools and tool history keep their current
protocol and take precedence over automatic backend workflows.

## Evidence flow

1. Bound and redact the supplied conversation before sending it to the planner.
2. Extract code fences, diffs and logs from user messages only. Synthetic snippet
   names identify pasted material; they are not claims about actual disk files.
3. A model selects a declared workflow. Engineering function parameters contain
   no source: the server binds the original redacted evidence independently.
4. A fixed unprivileged Python worker validates inputs and produces deterministic
   evidence, heuristic findings or a request for missing information.
5. A model with no tools synthesizes the result. Source comments, log text and
   retrieved material remain untrusted data; they cannot schedule another call.

Public responses retain `choices[0].message.content`. Engineering executions also
include `harmonika.reports[]` with structured worker evidence and scope. On SSE,
this metadata is included in the terminal completion chunk. Keepalive comments
are emitted while processing; synthesis is currently buffered until complete.

## Scope and truthfulness

- Code review is bounded static analysis of supplied snippets/diffs. Findings are
  risk signals needing contextual verification, not proof of exploitability or
  a guarantee that unflagged code is safe.
- Debug separates observed errors from hypotheses. No remote log/monitoring
  access or confirmed root cause is implied by matching an error pattern.
- Testing strategy generates a plan and cases. Tests, coverage, load and CI are
  never marked successful unless actual execution evidence exists separately.
- Documentation derives facts from supplied code or requirements. Missing setup,
  credentials, routes and configuration must remain explicitly unknown.
- Source reading, patching, executing tests and deployment in a user's project
  remain responsibilities of their authorized local tool runtime. This API does
  not gain arbitrary shell or server filesystem access.
- No GitHub private repository, Jira/Linear, Datadog, PagerDuty or messaging
  connector is enabled by this phase. A PR URL alone is not a fetched diff.
- No automatic learning or cross-customer conversation access is introduced.

## Limits

Shared input: request <=8,000 characters; context <=12,000; <=8 relative source
identifiers, each <=32,000 characters; diff <=64,000; logs <=32,000; canonical JSON
<=128 KiB. These are supplied identifiers, not paths to open on the server.
Automatic chat context <=240 KiB; the bounded extractor may clip earlier evidence
with an explicit warning. Public concurrency remains one job per key/two global,
three tool executions maximum, 150-second overall deadline and existing quota.
Read-only results are not persisted in the shared job database.

## Local interface

For an installed runtime containing this release:

    harmonika skills list --prefix engineering. --summary
    harmonika skills execute engineering.code_review --root /absolute/project

The execute command reads JSON from stdin, for example:

    {"request":"Review keamanan kode ini", "files":[{"path":"app.py","language":"python","content":"def hello():\n    return 'hello'\n"}]}

The local worker produces a deterministic structured report. The chat gateway
adds mentor synthesis. Availability of a model is distinct from static-worker
readiness. Prepared analysis is not execution or a completed fix.

## Q&A checkpoint

- The initial live test exposed a routing gap: “Review keamanan kode” did not
  match the earlier narrow “review kode” gate. The gate now accepts intervening
  words and pasted fenced evidence; the model still chooses the actual workflow.
- Backend evidence is bound from user turns rather than model-generated function
  arguments. Assistant text cannot introduce source files into the worker.
- An unavailable private connector produces a request for evidence, not a claim
  that a PR, repository or log system was inspected.
