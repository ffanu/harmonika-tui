# Dependent tool operations

A batch of model tool calls can execute concurrently. Listing a write before a read in the same response does not establish a dependency. The planner must wait for a write/edit result before a separate read, test or reload that consumes its output. Independent reads may still be batched.

The debugging workflow is reproduce, scoped backup, edit, tests, activation, live verification, write report, then read report. A failed verification script has not performed its checks; fix/preflight the script before reporting success. Unit test success alone does not prove the running service has loaded the repaired revision.

The owner API500 exam on 2026-11-11 demonstrated the race: a report read began4ms after its write started and18ms before the write completed. The filesystem could briefly report not found and then suggest the just-created exact same filename. A later read succeeded. This evidence does not establish an ignored-file access bug.

Planner guidance is active in backend remote-workflow.txt v2026-11-11.9 and the terminal harmonika-debug/global operator instructions. Backend text is hot-read per decision, checked for its version prefix and12000-byte bound, and deployed atomically with a hash guard and private rollback backup. The six backend integration tests verify that the rule reaches the model prompt before task data and preserve auth/CS/shared-memory boundaries.

A new native three-checkpoint write/read examination records actual completion times and requires each dependent operation to occur after the prior result. This is an instruction-level correction with observed regression coverage; no new hard scheduler lock or binary/ASCII/brand change is claimed. Production services and user sessions are not restarted or attached. Owner reports/traces remain private and are not general knowledge or training data.
