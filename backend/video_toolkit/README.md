# Harmonika video toolkit

Three isolated Windows RTX engines behind a private operator interface. Linux
remains the CPU gateway. The installer does not update an existing service,
GPU driver, browser profile or system Python.

| Engine | Purpose | Availability rule |
|---|---|---|
| WanGP / Wan 2.1 1.3B | Text to video | Private in-house use; WanGP license does not permit offering a paid API without separate permission. |
| FramePack F1 | Animate a reference image | Private qualification first; retain upstream model notices. |
| MMAudio | Generate sound conditioned on video | Research only: official weights are CC-BY-NC-4.0; broker rejects commercial/public soundtrack requests. |

## Layout

- Programs and independent Python environments: `C:\HarmonikaVideo\{engine}`.
- Control scripts, mechanical status and receipts:
  `C:\Users\Kraken\harmonika-video-toolkit-source`.
- Large model snapshots:
  `D:\HarmonikaAI\video-toolkit`.
- Private temporary jobs: `C:\HarmonikaVideo\jobs` (NTFS access controls).
- Models and sources are pinned in `models.json` / `sources.json` with SHA-256.
  No automatic updates or runtime model downloads.
- Three profiles use different NumPy/Transformers versions. Never combine venvs.
- Existing H3 wheel cache is read only; no H3/Flux/vision environment is modified.

## Readiness

`*-install.json` proves dependencies and CUDA probes. `*-models.json` proves
pinned model files. **Only `*-qualification.json` with `phase: verified` proves
actual inference.** Import checks alone must not activate a capability.

The original broker qualification is one-second video (Wan 50 steps, FramePack
25 steps) and eight-second synthetic MMAudio research. The separate owner AV
broker (`broker_av.py`, Windows18251 / PC1:18252) additionally exposes the
verified ten-second Wan profile: 480×272, 160frames, 20steps. The existing
one-second broker remains in place. Native visual generation is silent; the
PC1 owner narration adapter adds local CPU synthetic speech, independently
of research-only MMAudio.

## Windows operator procedure

Use Python 3.12 with isolated mode from the control directory:

```powershell
python -I install.py all
python -I download.py
python -I link_models.py
python -I startup_probe.py
python -I qualify.py wangp
python -I qualify.py framepack
# Private research qualification only, never commercial routing:
python -I qualify.py mmaudio
```

`coordinator.py` can run the qualification sequence as verified models arrive.
It does not automatically retry failed renders. Review the mechanical error and
correct the fault before explicitly re-running a synthetic qualification.
Only one GPU job may run at a time. Keep old workers idle during qualification;
never interrupt another worker to acquire GPU memory.

## Internal interface

`broker.py` binds Windows loopback `127.0.0.1:18237`. Requests require the private
service token, an owner hash and an idempotency key. It exposes capabilities,
job submission/status and owned artifacts; no public or paid route is enabled.
`client.py` prepares a stable PC1 operator interface via loopback port 18240.
The dedicated Linux tunnel service was deployed and its authenticated health,
unauthorized denial, offline fallback and shell-denied key were tested. It does
not register a route in the public API or restart the main gateway.

Inputs remain in RAM / temporary job files. SQLite and status logs contain
mechanical metadata only. The broker does not persist prompt text in SQLite.
It holds uncertain jobs after a restart instead of silently submitting again.

Tests on the development Mac:

```sh
python3 -m unittest discover -s tests/video_toolkit -v
```

Licenses: [WanGP](https://github.com/deepbeepmeep/Wan2GP/blob/97845dc88f43a9827097a18392f2318561d0d43b/LICENSE.txt),
[MMAudio weights](https://huggingface.co/hkchengrex/MMAudio),
[FramePack](https://github.com/lllyasviel/FramePack).
