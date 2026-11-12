# Native video companion

Keeps the existing signed owner transport, permissions, idempotency journal and
checksum-verified downloader. Only the advertised workflow guidance and returned
artifact quality metadata change. Actual readiness comes from the backend.

Build a signed release with `runtime/tools/tui_release.py` and the operator's
existing private Ed25519 key outside Git. Activate with
`scripts/activate-native-video.py --package <zip> --public-key <trusted-public.pem>`.
The branded native binary and running user sessions are preserved.

When replacing an installed companion, also supply `--previous-package <old zip>`.
Its trusted signature and every installed file must match before the old entry
can be replaced. Custom/modified MCP entries remain protected.
Rendering prompts prefer concise English and a supplied reference for exact
product appearance; user replies stay in the user's language.
