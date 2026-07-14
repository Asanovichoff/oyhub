FROM python:3.12-slim

WORKDIR /app
COPY oyhub/ oyhub/

# Dashboard extra (FastAPI + uvicorn) — the MCP server itself needs nothing.
RUN pip install --no-cache-dir fastapi uvicorn

# All state lives under /data — mount volumes to persist.
ENV OYHUB_HOME=/data/hub \
    OYHUB_VAULT=/data/vault

# MCP over stdio: clients spawn this container with -i.
# The compose 'dashboard' service overrides the entrypoint to serve the web UI.
ENTRYPOINT ["python", "-m", "oyhub"]
