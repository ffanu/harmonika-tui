# Harmonika private video routing

One async contract connects the owner terminal MCP and authenticated owner WebUI
to the isolated Windows video toolkit queue through PC1. Existing chat/image,
customer, APK/iOS, and public API contracts are unchanged.

## Paths

- Public authenticated terminal transport: `/v1/terminal/rpc`.
- Fixed worker hook: `/opt/harmonika-ai/crush-bridge/worker.py` imports `facade.py`
  per request. No core gateway restart is required.
- Web-only loopback transport: `127.0.0.1:18246/rpc`, signed with a separate
  owner-web secret. `harmonika-video-routing.service` runs `server.py`.
- RTX tunnel: `127.0.0.1:18240` → Windows loopback toolkit broker.
- MCP plugin: `~/.config/harmonika/video-tools/video-mcp.cjs`.
  New terminal sessions load one `video` tool with actions `capabilities`,
  `generate`, `status`, and `download`. Three existing media utilities are grouped
  under `media` to keep total native + MCP definitions within the gateway limit. Existing sessions are not terminated.

## Contract and restrictions

Submit once with a stable 32-hex `request_id`. The response is an asynchronous
job handle, never a completed-video claim. Poll the same request/job; ambiguous
submission uses GET-only reconciliation and never automatically re-submits.
Completed MP4 downloads must match the broker's SHA-256. Files are created
exclusively; different existing output files are not overwritten.

The owner terminal supports two verified silent visual profiles: **1 second,
832×480** and **10 seconds, 480×272**. Ten-second qualification produced 160
real frames; no repeated one-second clip is substituted. The AV broker uses a
separate loopback18251 on Windows and an authenticated forward on PC1:18252;
GET-only lookup falls back to the older broker for existing jobs.

`media` action `synthesize_speech` produces real local WAV speech in Indonesian
or English using the CPU. This is a **synthetic voice**, not natural or cloned.
`video` action `narrate` accepts a completed owned job and text, verifies the
source MP4, creates TTS and muxes an AAC track into a new MP4. Speech longer than
the video is rejected rather than truncated; the remainder is padded with
silence. The raw GPU output remains silent. Text never enters process argv,
SQLite or request logs; private temporary files are removed after processing.

WanGP's private/internal license gate remains enabled; this is not unrestricted
public or paid video access. MMAudio stays research-only and is not used for
commercial soundtracks. Production WebUI remains a separately staged change;
this activation updates the owner terminal RPC/MCP contract.

Both transports require an HMAC owner proof in addition to their outer login/API
authentication. Configuration/secrets stay outside Git. SQLite records only
client/nonce/expiry for replay protection; the local terminal recovery journal
stores only request/job IDs and states. Prompts, credentials and customer history
are not written to these stores or request logs.

## Verify

```sh
python3 -m unittest discover -s tests/video_routing -v
node --test tests/video-routing.test.cjs
```

Web patch and its focused HTTP/Range tests live in the separate
`harmonika-hai-video` workspace. Production WebUI activation needs a source hash
match and the explicit existing-process restart exception required there.
