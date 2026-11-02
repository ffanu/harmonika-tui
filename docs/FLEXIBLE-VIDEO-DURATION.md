# Flexible video duration

Active owner RPC on chat.harmonika.id, TUI release 2026.11.01.5.
The signed video RPC interface is unchanged. `video action=animate` accepts an
approved local reference_file, prompt and positive integer duration_seconds.
Requests exceeding the available storage reserve are rejected explicitly.
The CPU gateway serializes <=10-second Windows RTX segments, conditions each
segment on the prior final frame and verifies the exact concatenated duration.
Short tails are generated at >=3 seconds and trimmed to their requested span.
No freeze-frame/loop/speed trick is used to extend duration.

The real 10-second RTX test completed with 160 frames, 512x288, no audio.
The 11/13-second multisegment orchestration was tested using a synthetic CPU
worker; arbitrary long GPU videos have not all been benchmarked. Scene/character
continuity is best effort. The first 10-second GPU qualification took about nine
minutes. Existing legacy text profiles remain available. Add audio/edit via Studio.

## Runtime

- New isolated service: harmonika-flex-video.service, port 18258, loopback only.
- Source: /opt/harmonika-ai/flex-video/{engine,server}.py.
- Private state: /var/lib/harmonika-flex-video; temporary inputs mode0600.
- Existing /opt/harmonika-ai/video-routing/facade.py hot-loads flex_video_proxy.py
  after the existing owner signature and input-field validation.
- Facade backup: facade.before-flex-20261101.py in that same directory.
- The original PC2 gateway and video workers were not modified/restarted.
- New terminal artifacts download in 512-KiB ownership-checked chunks and verify
  the final SHA-256 before publication. Retention removes terminal artifacts at24h.
- SQLite contains mechanical metadata; no raw prompt, image, or chat history.
- Ambiguous remote submissions/restarts require reconciliation; no blind retries.

## Verification

python3 -m unittest discover -s tests/flex_video -v
node --test tests/video-routing.test.cjs tests/compact-core-tools.test.cjs

See docs/verification/flex-video-20261101/report.json. Open a new TUI process to
refresh cached tool schemas; existing user sessions are not terminated.
