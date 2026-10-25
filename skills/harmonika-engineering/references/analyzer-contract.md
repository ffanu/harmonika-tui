# Engineering analyzer contract

`skills/harmonika-engineering/scripts/engineering_analyzers.py` exposes
`analyze(operation, payload) -> dict` for `code_review`, `debug`,
`testing_strategy` and `documentation`. Input reaches it only through
`engineering_evidence.prepare(payload)`; see that module for payload validation,
redaction and the `{id, source, path, line, text}` evidence excerpt.

## Response

| Key | Value |
| --- | --- |
| `schema_version` | `1` |
| `operation` | the requested operation |
| `status` | `completed`, `degraded`, `clarification_required` |
| `method` | `{analysis: 'bounded_static_heuristics', code_executed: false, repository_fetched: false}` |
| `evidence` | cited lines first, then remaining prepared excerpt, ≤200 items |
| `findings` | ≤40 items, ordered by severity then path and line |
| `report` | per-operation structure, always including `inputs` |
| `limitations` | base limitations, per-operation limitations, plus anything that was bounded away |

A finding is `{id, rule, dimension, severity, kind, title, source, path, line,
evidence_id, evidence, why, recommendation}`. `severity` is `high`, `medium`,
`low` or `info`; `kind` is `observation` (the pattern itself is the fact) or
`hypothesis` (a candidate that needs checking). `evidence_id` always resolves in
the returned `evidence` list: ids from preparation are reused, and ids this
analyzer registers are prefixed `analyzer-`.

`source` is `file`, `diff`, `logs`, `request` or `context`. For `file` the path
and line are the supplied identifier and its original line number. For `diff`
they are the post-change file and line, and only added lines are reported;
context lines are read for structure only. Log citations use the identifier
preparation assigns to the log text.

`status` is `clarification_required` when the operation has no usable input
(`code_review` without files or diff, `debug` without diagnostics) — the
questions are in `report.missing_context`. It is `degraded` when the analysis
itself was bounded: findings or evidence hit a cap, a region exceeded 4000
lines, or a malformed payload entry was skipped. Redaction and a capped
preparation excerpt are normal and reported in `limitations` instead.

## Checks

24 line checks, 9 context checks and a cross-file duplicate-block check.

| Rule | Dimension | Severity | Kind | Title |
| --- | --- | --- | --- | --- |
| `assert_used_for_validation` | correctness | low | observation | assert used outside a test module |
| `bare_except` | correctness | medium | observation | Bare except catches every exception |
| `check_then_act_race` | correctness | medium | hypothesis | Check-then-act sequence open to a race |
| `float_money` | correctness | low | hypothesis | Monetary value converted to binary floating point |
| `mutable_default_argument` | correctness | medium | observation | Mutable default argument shared across calls |
| `off_by_one_risk` | correctness | medium | hypothesis | Loop or index bound includes the length value |
| `swallowed_exception` | correctness | medium | observation | Exception discarded without logging or re-raise |
| `unchecked_division` | correctness | low | hypothesis | Division by a computed count without a visible zero guard |
| `duplicated_block` | maintainability | low | observation | Duplicated block of six or more significant lines |
| `long_function` | maintainability | low | observation | Long function body |
| `todo_marker` | maintainability | low | observation | Unresolved work marker in the reviewed text |
| `missing_timeout` | performance | medium | hypothesis | Outbound request without an explicit timeout |
| `n_plus_one_query` | performance | high | observation | Data-access call inside a loop (N+1 pattern) |
| `nested_membership_scan` | performance | medium | hypothesis | Linear membership test inside a loop |
| `network_call_in_loop` | performance | medium | observation | Network call inside a loop |
| `sequential_await_in_loop` | performance | medium | observation | Awaited call serialises the loop |
| `string_concatenation_in_loop` | performance | low | observation | String rebuilt on every iteration |
| `unbounded_result_set` | performance | low | hypothesis | Query appears to fetch an unbounded result set |
| `unreleased_resource` | performance | medium | hypothesis | Resource opened without a visible release |
| `command_injection` | security | high | observation | Shell invocation on a path that can carry input |
| `credential_assignment_redacted` | security | low | hypothesis | Credential-shaped assignment was redacted before analysis |
| `cross_site_scripting_risk` | security | high | observation | Unescaped value rendered as markup |
| `csrf_protection_disabled` | security | medium | observation | CSRF protection disabled for a handler |
| `debug_mode_enabled` | security | low | observation | Debug mode enabled in source |
| `dynamic_code_evaluation` | security | high | observation | Dynamic evaluation of a code string |
| `hardcoded_credential` | security | high | observation | Credential-shaped literal assigned in source |
| `insecure_randomness` | security | medium | hypothesis | Non-cryptographic randomness used for security material |
| `path_traversal_risk` | security | medium | hypothesis | Request-derived value used to build a filesystem path |
| `shell_invocation` | security | medium | observation | Command executed through a shell |
| `sql_string_interpolation` | security | high | observation | SQL statement built by string interpolation |
| `ssrf_risk` | security | medium | hypothesis | Request-derived value used as an outbound request target |
| `tls_verification_disabled` | security | high | observation | Transport certificate verification disabled |
| `unsafe_deserialization` | security | high | observation | Deserialisation of untrusted-capable format |
| `weak_hash_for_secret` | security | medium | hypothesis | Weak digest applied to credential-like material |

Loop scope is tracked by indentation for Python-like text and by brace depth for
C-like text, so a query inside a loop is distinguished from the same call
outside one. Eight positive checks (`parameterised_query`,
`context_managed_resource`, `specific_exception_handling`,
`explicit_input_validation`, `secret_from_environment`, `explicit_timeout`,
`bounded_query`, `type_annotations`) populate `report.what_looks_good`; each is
one matched line, not a judgement about the file.

## Debug specifics

Fifteen operational signatures are recognised: `connection_refused`, `timeout`,
`out_of_memory`, `permission_denied`, `missing_file_or_module`,
`too_many_open_files`, `lock_or_deadlock`, `authentication_failure`,
`tls_failure`, `rate_limited`, `integrity_conflict`, `pool_exhausted`,
`disk_full`, `null_dereference`, `type_or_key_error`. Each contributes an
observed fact plus hypotheses that each carry a `discriminating_test`. Python,
Java, JavaScript and Go stack frames are parsed after a timestamp or level
prefix is stripped, then matched against supplied file paths;
`report.root_cause` is `location_identified` only when a frame maps to a
supplied line, and `report.proposed_fix.status` is always `withheld`.

## Testing and documentation specifics

Test cases are derived from function signatures (parameter-name heuristics for
boundary cases), `raise`/`throw` sites, declared routes, external-boundary
imports and entrypoints; every case cites a path, line and evidence id.
`coverage.measured` is always false. Documentation extracts definitions,
docstring presence, route declarations, environment variables, CLI commands,
entrypoints and error types; the outline marks every ungrounded section
`requires_input` with what the owner must supply.

## Tests

`tests/test_engineering_analyzers.py` runs against the real
`engineering_evidence` when reachable (integrated `runtime/tools` copy,
`HARMONIKA_ENGINEERING_EVIDENCE=<path>`, or the sibling worktree) and otherwise
against a stub fixture of the prepare contract;
`HARMONIKA_ENGINEERING_EVIDENCE=stub` forces the stub. The run prints which one
was used.
