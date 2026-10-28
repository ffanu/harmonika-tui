FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home connector
WORKDIR /app
COPY backend/remote_access/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY backend/remote_access /app/remote_access
COPY runtime/tools/ssh_connector.py /app/remote_access/ssh_connector.py
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 HARMONIKA_SSH_CONFIG=/run/remote/targets.json HARMONIKA_REMOTE_TOKEN_FILE=/run/remote/api.token HARMONIKA_REMOTE_APPROVAL_FILE=/run/remote/approval.token HARMONIKA_REMOTE_ROOT=/data HARMONIKA_SSH_STATE_DIR=/data/worker-audit
CMD ["python","-m","remote_access.api"]
