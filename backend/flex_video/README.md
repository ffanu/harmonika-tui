# Flexible video duration

Internal owner pipeline: positive integer seconds, sequential RTX segments of at
most 10 seconds, last-frame conditioning, exact-duration CPU trim and concatenation.
No promise of infinite capacity or seamless scene continuity. Disk admission reserves
1 MiB per requested second plus 2 GiB; insufficient storage is rejected explicitly.
Outputs are 512x288/16fps silent previews. Studio can add narration and edit the output.

The original qualified 1/10-second text profiles remain accessible. New image-to-video
and other durations use the current Windows ComfyUI gateway. A running/queued job
is not a completed video. GPU availability and model errors remain explicit.

SQLite stores only owner hash, IDs, digest, requested seconds, state and progress.
Private inputs are files with mode 0600 and removed at terminal state. Unknown work
is never automatically re-submitted after a timeout/restart. Artifacts download in
512-KiB authenticated, ownership-checked chunks with a final SHA-256 verification.
