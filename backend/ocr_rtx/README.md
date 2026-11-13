# Local high-quality OCR recovery

Recorded checks: [`OCR-RTX-RECOVERY-20261113.json`](../../docs/OCR-RTX-RECOVERY-20261113.json).

The shared OCR API is `https://chat.harmonika.id/v1/ocr`. It routes image/scan
extraction to the stateless Windows RTX worker: Qwen3-VL 8B is preferred when
VRAM permits, with the already-installed 2B model as a smaller local option.
Linux PaddleOCR PP-OCRv5 remains the explicitly reported CPU fallback. The
public API and native terminal continue to use this existing gateway/auth path.

The repaired failure was an absent Ollama listener on Windows loopback 11439
while the OCR HTTP worker on 18202 remained alive. Model files were intact on C:.
The runtime now runs under a dedicated SYSTEM startup task and the existing RTX
supervisor/watchdog, with PT0S, IgnoreNew and 999 restart attempts. Legacy runtime
launchers request that task rather than starting competing model servers.
The existing OCR worker keeps its operator account, receives the same recovery
settings and is included in the watchdog; its running process is not stopped.

## Deploy

Requires the existing pinned `ollama-v0.40.11`, model directory in
`C:\Users\Kraken\harmonika-vision-worker`, Supervisor/ChildRuntime in ProgramData,
and the existing `owned_job.py` Windows Job Object containment helper.

1. Create a private `C:\Users\Kraken\harmonika-ocr-runtime` directory for the
   operator, SYSTEM and Administrators. Copy these three Python files there.
2. Confirm port 11439 is free, then run `python install_runtime.py` once. It
   refuses to overwrite an existing configuration or adopt an existing listener.
3. Run `python harden_startup.py`; it backs up recognized legacy launchers/task
   settings before updating them. Unrecognized operator scripts are preserved.
4. Authenticate health checks through the existing private worker token. Confirm
   a real image extraction reports `qwen3-vl-8b-rtx-local` and GPU execution.
   A listener/HTTP 200 alone does not prove OCR inference or accuracy.
5. Verify the shared API and actual native terminal on synthetic text, invoices,
   tables and scanned PDFs. Report each actual engine and any CPU fallback.

The model server receives no inherited API keys, tokens or passwords; it binds
only to loopback, disables cloud/history, and discards both native output streams.
Windows Job Object containment applies only to children created by this shim.
The existing supervisor records fixed technical metadata, never document content.
D: remains offline. No other user's process or terminal is stopped.

## Native terminal adapter

`runtime/tools/shared_ocr.py` uses the existing HTTPS API/key/device checks. The
deployed helper is replaced atomically with a verified hash and a private backup.
The branded binary, existing `mcp_server.py`, permissions and other tools remain
intact. Launching a fresh Harmonika process loads the updated adapter; a running
MCP process may retain its previously imported Python helper until reconnected.

OCR functions: `ocr_document`, `analyze_image` with `mode=ocr`, and scanned PDF
reading via `document_studio`. Images are PNG/JPEG/WebP; the existing API limits
remain 10 MiB and five PDF scan pages. Scene understanding uses `mode=vision`.

The API's O/0 ambiguity markers are preserved. The updated TUI helper also marks
I/l/1 confusion in selected ASCII code-shaped identifiers, including per-page
text. It preserves the unverified raw spelling and never guesses a correction.
This extra review runs in the TUI adapter; it is not a server-wide OCR schema
change. Review flags do not establish document authenticity or perfect accuracy.

GPU/model transitions can produce an explicitly reported CPU fallback. OCR is
stateless and does not automatically become model training. Input/document text
and customer data must not enter runtime watchdog/error reports.

Focused checks: `python3 -m unittest discover -s tests -p 'test_ocr_*.py' -q`.
