# SSH execution contract

The terminal uses registered aliases and a private connector. It must distinguish connection failure from command failure and must not claim that a successful connection proves application health. The owner installation loads its Python MCP adapter; this helper source documents the tested execution contract rather than replacing the binary or unrelated JavaScript runtime.

| Result | Meaning | Next action |
|---|---|---|
| connection_status=failed, execution_state=not_started | Authentication/transport/host-key setup failed before a command was sent | Stop SSH attempts for that target; report the specific technical blocker |
| connected, completed, exit_status=0 | A command finished successfully | Interpret its actual output and scope |
| connected, completed, nonzero exit (including 255) | Remote command failed; SSH itself connected | Read stdout/stderr separately; do not call it a transport failure |
| connected, unknown, exit_status=null | Command may have run or still be running | Preserve partial evidence; never automatically repeat a mutation |
| all_checks_executed=true, collection_status=incomplete | Every baseline command was attempted, but output was truncated | Narrow the relevant query before claiming complete evidence |

`health_status=not_assessed` is intentional. A failed unit can be present even when every diagnostic exits zero. A failed `cd` prevents the requested command from running; cwd does not persist across calls.

The helper accepts one bounded JSON object, validates fields before opening a socket, limits stdout and stderr independently, drains both streams and returns structured errors. Invalid JSON/types do not produce a traceback. Transport exception messages and Paramiko thread logs are not forwarded to the model. Fingerprint results distinguish pinned from not_configured. Pin enrollment requires an independently trusted key; the owner test matched the existing Mac known_hosts and tested both accepted and mismatched keys. No key/password belongs in examples or Git.

Run focused tests from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_*ssh*.py' -q
```

The 2026-11-11 owner exam used separate native Harmonika sessions/XDG directories and read-only server checks. It verified a real live Ubuntu audit, an unreachable registered LAN target, remote nonzero exit, invalid cwd, partial-output timeout, untrusted remote instructions and host-key mismatch. Reports/backups are private outside this repository. The inaccessible LAN server was not audited successfully. CPU saturation and failed services are server findings, not authorization to restart, kill or change those services.
