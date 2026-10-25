# Harmonika coding sandbox image (OpenCode-owned).
# Official Node.js base; adds Python 3 and git for sandbox jobs. No GPU.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY volume_io.py /opt/harmonika/volume_io.py
RUN chmod 0444 /opt/harmonika/volume_io.py \
    && chown root:root /opt/harmonika/volume_io.py

USER 10000:10000
ENV HOME=/tmp
WORKDIR /workspace
ENTRYPOINT []
