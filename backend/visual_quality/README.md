# Local visual quality and native design QA

Windows RTX runs the image gateway and a pinned Qwen3-VL 8B visual inspector. Linux remains the shared `/v1/ocr` entry point, using RTX first and an explicitly reported local CPU fallback. The TUI uses `analyze_image mode=vision` for composition and `ocr_document`/`mode=ocr` for text. The image gateway currently reports **FLUX.2 Klein 4B** for its `fluxhd` profile; do not label that result FLUX.1-dev.

The visual workflow creates real raster assets, composes exact text in editable HTML/CSS, exports browser PNGs at deviceScaleFactor 1, and checks visible pixels, source text, dimensions, mobile overflow and contrast. PNG megabytes alone do not measure quality. Use `skills/harmonika-visual-design/SKILL.md` and `VISUAL-ROUTING.md` as app instructions.

## Windows worker

Deploy `vision_worker.py`, existing `backend/rtx_vision/worker.py` as `base_worker.py`, and `install_worker.py` into a new private C: directory. Provision `worker-config.json` with the existing vision connector token through the private vault/connector, never source control or command arguments. Reuse the existing local Ollama 11439 and the exact 8B digest pinned in source; verify the model independently before deployment.

`install_worker.py` adds SYSTEM boot task `HarmonikaVisionQuality`, supervisor `VisionQuality`, unlimited execution time, restart settings and the existing watchdog. It refuses an occupied port or existing installation. It backs up the legacy worker launcher and delegates future starts to the new task. It does not stop original services, unload other GPU jobs, reset the GPU, or mount D:.

Authenticated protocol stays `/v1/rtx/health`, multipart `POST /v1/rtx/jobs`, and `GET /v1/rtx/jobs/{id}` on loopback 18188. GPU admission fails explicitly when busy. Completion requires the correct returned model, pinned digest and GPU residency. Do not put output/prompt data in watchdog logs. Results expire from RAM after 30 minutes, at most 128 observations; SQLite WAL stores only job mechanics/digests and **NULL text**. On restart an expired result returns unknown, never fabricated success. Runtime stdout/stderr and HTTP access logs are not retained.

## Owner TUI activation

Clean modules live under `runtime/tools`. Run `scripts/activate-visual-quality.py` on the authorized Mac owner installation. It checks known adapter anchors, compiles a candidate, backs up the private MCP file outside Git, and atomically replaces only image generation provenance and vision transport functions. Rerunning refreshes changed helper modules even when MCP functions already delegate, while preserving the private MCP bytes. Unchanged reruns do not create backups. Use --owner only for an explicit owner directory or a test fixture. Provision the existing key-authenticated `win-kraken` SSH alias with strict known-host verification.

The verified owner installation additionally includes `media_tunnels.py` as a launchd task for ports 18200/18188, adds the visual skill/routing file to existing skill paths, and registers the native package in both `harmonika.json.plugins` and `cli.json.plugins`. It installs `context_guard.mjs` with `context_window.cjs` as a provider-scoped native plugin. These owner config changes preserve all existing MCP tools, brand, binary, providers and unrelated services. Back up private config before any edit. Existing already-open TUI processes can retain old modules; new private native launches (`harmonika --standalone`) are the verified activation boundary. The persistent user server was not restarted.

`projection-state.json` contains only last-request character counts, role counts, timestamp and whether projection ran; it contains no session ID or text. Historical binary screenshots are replaced by explicit unavailable-media markers, preventing multi-megabyte image history from crowding out later revisions.

The context guard uses the native v2 `http.request` hook and modifies only the transient JSON request. It targets a bounded historical transport size, preserves latest user content and tool declarations, keeps tool call/result pairs, retains prior user requirements, and labels omitted text as unavailable evidence. Image-only user messages inserted by tool adapters stay within the current textual task, so its QA receipts are not discarded as history. This prevents repeated image reads caused by losing the immediately preceding inspection evidence. Durable sessions remain intact. Mandatory oversized current content is never silently clipped. When it exceeds the soft 150,000-character target, safe historical reductions are retained instead of restoring the original oversized request; backend admission remains authoritative. This is a transport projection, not model training or a persistent customer conversation summary.

The OCR adapter also preserves overlapping O/0 and I/l/1 review in one identifier, without nesting markers or treating a raw OCR spelling as verified. This is a conservative selected-ASCII guard, not a confidence estimate or an authenticity check.

The shared Linux asynchronous image queue did not advertise a verified vision route at audit time. The recovered Windows inspector and Mac native path were tested directly; enabling a separate Linux queue vision route requires its own validation and activation. Do not infer web readiness from native QA.

## Verification

```sh
python3 -m unittest discover -s tests -p test_visual_quality.py -v
python3 -m unittest discover -s tests/rtx_vision -p test_worker.py -q
python3 -m unittest discover -s tests -p 'test_ocr*.py' -q
node --test tests/visual-context.test.cjs
```

Run real native exams with synthetic images before handoff: product photo, responsive website, exact-copy poster, same-session revision, and dense OCR table. Review actual artifacts and each answer. A successful tool call does not prove aesthetic quality, OCR certainty or authenticity of an ID.

## Long mockups and acceptance checks

The native adapter accepts static PNG/JPEG/WebP up to 10 MiB, 16,777,216 source pixels and a 16,384-pixel edge. Inputs exceeding the worker 4096-pixel edge are split into pixel-preserving 2048-pixel strips with 128-pixel overlap, at most 12 strips. Jobs are sequential under one 180-second deadline; each request is bounded to 15 seconds or remaining time. Pending jobs retain IDs. No crop files or prompts are written to mechanical logs.

Every completed strip requires the expected local 8B model, engine, GPU flag and nonempty observation. Failure returns coverage_complete=false and completed_tiles; partial observations never approve the entire canvas. Successful coverage establishes supplied source regions, not perfect model comprehension or a global aesthetic judgment. Inspect the overview separately. Camera-angle claims require visible front/side evidence and neutral QA; unmet criteria remain unmet after an unsuccessful revision.

```sh
python3 -m unittest discover -s tests -p test_mockup_tiles.py -q
python3 -m unittest discover -s tests -p test_visual_activation.py -q
```
