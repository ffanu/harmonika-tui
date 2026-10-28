# Harmonika local neural speech

CPU ONNX voice adapter for the existing owner RPC/MCP contract. Indonesian and
English, with bundled `wanita`/`pria` presets; no reference voice uploads, cloning,
paid cloud dependency or GPU driver changes.

## Runtime

- PC1: `/opt/harmonika-ai/neural-speech`, isolated Python environment.
- New service: `harmonika-neural-speech.service`, loopback `127.0.0.1:18256`.
- CPUExecutionProvider only, four intra-op threads and one inter-op thread.
- CPU quota400%, memory cap4GiB, no device access.
- Runtime model download is disabled. Startup validates every model/voice SHA256.
- Model: archived Supertonic3, immutable revision
  `aafc6e32416a594460b32413efc49d7fe4ce6d46` (19 files,401301114bytes).
- SDK1.3.1, CPU ONNX Runtime1.24.4; installed dependency download hashes are
  recorded in the private runtime pip report.
- The upstream is archived; deployment remains pinned and updates are manual.

## Interface

`media` action `synthesize_speech` takes text, optional language`id`/`en`,
voice`wanita`/`pria`/`wanita-2`/`pria-2`, quality`neural`/`basic`, optional output
file. `video` action `narrate` accepts the same voice parameters with a completed
owned video job. No extra MCP definitions are added: native plus MCP stays31.

The owner policy `/etc/harmonika-ai/speech-policy.json` defaults to neural.
All four presets (`wanita`, `wanita-2`, `pria`, `pria-2`) are approved by the user; the default remains `wanita` when no gender is requested. There is no
silent downgrade to the old robotic engine. `quality=basic` is an explicit
fallback. Neural WAV volume is normalized; speech exceeding video duration is
rejected rather than cut off.

## Privacy and failure handling

Fixed loopback URL and root-private bearer secret. Text is processed in RAM,
never placed in command argv or logs. Private normalization/mux temporary files
are cleaned up. Service logs contain only success/latency and fixed technical
events. One inference at a time; concurrent calls get429 without queueing.
Missing/unavailable neural runtime yields a structured error, not a basic voice.

## Qualification

Two synthetic Indonesian promo samples were generated at44100Hz, about8.081s,
with no clipping. Independent existing ASR reproduced all reference words
(case/punctuation normalized), including Harmonika. Both10s H.264/AAC narrated
MP4s fully decoded; the female path was exercised through a native Harmonika
terminal invocation. These are technical checks, not human listener approval.
The user must listen before a voice is called the approved advertising voice.

## Licenses and sources

Code: MIT. Model: OpenRAIL-M, with its use restrictions and synthetic-content
notice obligations. Preserve SDK-LICENSE.txt and MODEL-LICENSE.txt. Returned
results disclose synthetic audio; do not imply a real speaker or consented
voice clone. No public/paid API entitlement is added by this owner-only change.

Sources: https://huggingface.co/supertone-oss-archive/supertonic-3
and https://github.com/supertone-oss-archive/supertonic .

```sh
python3 -m unittest discover -s tests/neural_speech -v
python3 -m unittest discover -s tests/video_routing -v
node --test tests/speech-tools.test.cjs tests/compact-core-tools.test.cjs tests/video-routing.test.cjs
```

## Promotional delivery

Both voices support the owner-side `style=promosi_ceria` presentation preset,
with warm invitational copy, up to three phrase renders and160ms inter-phrase
pauses. A small default pace adjustment accompanies phrase delivery; no pitch
shift, voice clone, undocumented emotion tag or native happy control is claimed.
The preset preserves every supplied word and adds no prices/coverage promises.
Its maximum input is600 UTF-8 bytes. Long audio is rejected rather than cut to
fit the video. `normal` remains the default for general speech and support.
New MCP sessions expose the style under the existing media/video tools, without
increasing the31-tool budget. Runtime service/model restarts are unnecessary.
Emotion/advertising suitability requires the user's listening evaluation.

Voice labels: Wanita1=`wanita`, Wanita2=`wanita-2`, Pria1=`pria`, Pria2=`pria-2`. The orchestration instructions follow an explicit voice request first, otherwise choose for context and preserve the chosen speaker throughout one narration. Both additional voices passed real owner RPC synthesis/download and WAV decode checks.
