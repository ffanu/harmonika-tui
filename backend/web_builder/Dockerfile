# Shared sandbox guarantees retained; new image only, existing coding image unchanged.
FROM harmonika-coding:20261024-phase2
USER root
COPY python.lock /opt/harmonika/builder-python.lock
RUN /opt/harmonika/tools-venv/bin/pip install --no-cache-dir --require-hashes -r /opt/harmonika/builder-python.lock
COPY package.json package-lock.json /opt/harmonika/web/
RUN cd /opt/harmonika/web && npm ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund \
 && apt-get update && apt-get install -y --no-install-recommends chromium \
 && rm -rf /var/lib/apt/lists/* \
 && /opt/harmonika/tools-venv/bin/pip freeze > /opt/harmonika/web/python.freeze
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    HARMONIKA_WEB_NODE=/opt/harmonika/web/node_modules \
    NEXT_TELEMETRY_DISABLED=1
USER 10000:10000
WORKDIR /workspace
