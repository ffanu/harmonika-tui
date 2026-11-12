# Play Console situation engine

The reusable pure engine (`engine.py`) defines developer/app scope, an explicit
read-only route profile, evidence-bound findings and Android archive preflight.
The owner MCP adapter (`runtime/tools/play_console.py`) reads the authorized
MacBook Chrome session and writes private audit reports locally. The collector
does not send page data to the domain-rule installer, shared training memory or
technical journals. The authorized TUI analysis receives private tool results
within its conversation; this does not imply that all model inference is local.
Canonical rules are installed on Linux and copied into the owner runtime as
`play_console_engine.py`; packaging checks use that same source.

Use `desktop_browser` with `action:play_console`:

- `operation:inspect`, `tab_id`: actual content area, notifications and field limits.
- `operation:audit`, `tab_id`, `output_dir`, optional `max_pages` (1–30): read
  rendered notifications, expand each accordion and capture its content while
  open; discover apps from the home table rather than a cached sidebar; inspect
  actual discovered policy/release/testing/content/integrity/store pages and
  short release links only when their app was verified in the current developer
  account. Save private JSON evidence and return prioritized proposed actions.
- `operation:preflight`, `path`, optional `expected_package`: inspect a specifically
  selected AAB/APK ZIP, size, hash and expected entries without extracting it.
  Manifest/package/versionCode/signing/targetSDK remain unverified until an actual
  decoder/toolchain check is supplied. ZIP validity never means ready to upload.

All operations are read/preview only. The adapter never creates a release,
uploads files, edits copy, saves settings, declares trader status, deletes
notifications or submits/publishes. A narrow Chrome click exception handles
Play Console's non-form notification navigation buttons with default submit type;
real forms and publication actions remain blocked. Opening a notification may
naturally change its unread indicator; no bulk mark-read action is sent.

Coverage distinguishes discovered/read/pending pages, rendered notification
completeness, truncation and unverified historical or paginated content. A stopped
or partial audit is not complete. Notification publication does not imply the
production track is live; current track, testing gates and version evidence govern
the next step. Broken placeholder links are reported, not blindly followed.

Reports use a new private directory under Projects or Downloads (0700/0600),
not the repository. Do not check account/application evidence into Git. Only
synthetic tests belong in source control. Restore the original URL only while
the tab still belongs to this audit; stop if the operator changes it.
