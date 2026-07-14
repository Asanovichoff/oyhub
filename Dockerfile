FROM python:3.12-slim

WORKDIR /app
COPY oyhub/ oyhub/

# All state lives under /data — mount volumes to persist.
ENV OYHUB_HOME=/data/hub \
    OYHUB_VAULT=/data/vault

# MCP over stdio: clients spawn this container with -i.
ENTRYPOINT ["python", "-m", "oyhub"]
