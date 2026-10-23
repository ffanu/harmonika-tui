---
name: harmonika-git
description: Inspect and prepare scoped Git changes safely; commit or push only when the user explicitly requests it.
---

Use for Git status, review, staging, commits or repository handoff. Read repository instructions and README; inspect `git status --short` and relevant `git diff` before changing files. Identify the repository and active branch. Preserve pre-existing changes and scope commands to the requested files; avoid broad staging when unrelated work exists.

Use native bash with Git. Inspect staged diff before any authorized commit; run focused project checks and summarize their results. Respect configured project identity; do not rewrite global Git settings. Commit only when explicitly requested, and push only when explicitly requested. A request to inspect or fix code does not authorize either. Before an authorized push, inspect the intended remote/branch and publish only the requested work.

Do not use destructive reset, clean, checkout of modified files, history rewriting, force-push or automatic stash. On conflicts, stop the unsafe operation and explain the affected files and options without discarding work. Never expose remote URLs containing credentials. Return the changed files, verification, branch/commit when actually created, and any remaining local modifications.
