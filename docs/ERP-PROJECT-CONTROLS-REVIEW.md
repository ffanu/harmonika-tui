# Project controls review —2026-10-26

Codex implementation; OpenCode GLM5.3 independent read-only review. Claude not used.

OpenCode verified default-deny RLS/composite project FKs, serializable revision CAS,
authorization before replay, terminal state and project completion gates. It raised
these findings, addressed as follows:

1. Linked task cancellation leaves a target unreached: explicit policy preserves
   the meaning of DONE. The target can be cancelled with a reason and replaced.
   Tests prove cancellation never pretends completion and owner cancellation works.
2. Project cancellation freezes child controls: explicit audit policy retains OPEN/
   PENDING historical states, rather than fabricating successful resolution. Tests
   verify frozen states and denial of subsequent child mutation. README documents it.
3. Future soft-deleted linked tasks: eligibility now rejects deleted tasks, including
   those previously DONE. There is currently no task delete endpoint.
4. Empty resolution/cancellation prompts: client rejects empty input with clear text.

Added real DB tests cover role downgrade before replay: a former admin who is still
assigned cannot replay previous owner-only issue closure or task cancellation.
Project close versus new issue creation is tested for exactly one successful writer.
Browser tests cover task-linked target eligibility and issue progress/resolution/
owner closure on desktop/mobile. No external notifications, business DB, production
project deployment or credentials are involved. These are qualified source workflows.
