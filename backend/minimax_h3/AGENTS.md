# MiniMax H3 installation boundaries

- Read README. Scope is the new Windows H3 runtime only. Never modify existing
  Flux/vision environments, user processes, GPU drivers, firewall or routes.
- PC1 Linux remains CPU gateway. GPU inference is on the registered Windows PC.
- SSH host keys must be verified. Never put credentials in repo, logs or receipts.
- Pinned source revisions and SHA-verified model/wheel manifests only. Do not run
  mutable upstream install/update/reset scripts or import optional custom nodes
  without inspection. Keep the launcher source attribution intact.
- Only tasks/processes started by this installation may be stopped/retried. Never
  interrupt another ComfyUI queue or issue global unload/interrupt requests.
- Use a checked free loopback port other than 3000; no public unauthenticated
  ComfyUI exposure. No customer chat/media is used in qualification or logs.
- An installed Python environment or downloaded weight is not a healthy inference
  engine. Real synthetic generation, media inspection and truthful degraded state
  are required before registering automatic shared routing.
