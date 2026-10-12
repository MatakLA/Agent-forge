# ==============================================================================
# Agent-Forge Demo Dashboard Container Image
# Runs the Agent-Forge web dashboard and admin console in demo mode.
# Python standard library only; no node, no worker image, no docker-in-docker,
# and no egress proxy service.
# ==============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/agent-forge/bin:${PATH}" \
    PYTHONPATH="/opt/agent-forge/bin/lib:/opt/agent-forge/web"

# Run as non-root user 'forge' (UID 1000) for safe local evaluation
RUN groupadd -g 1000 forge \
 && useradd -u 1000 -g forge -m -s /bin/bash forge \
 && mkdir -p /opt/agent-forge/bin/lib /opt/agent-forge/etc /demo-data \
 && chown -R forge:forge /opt/agent-forge /demo-data

WORKDIR /opt/agent-forge

# Copy application files from repository build context
COPY --chown=forge:forge web/ /opt/agent-forge/web/
COPY --chown=forge:forge bin/lib/foreman/ /opt/agent-forge/bin/lib/foreman/
COPY --chown=forge:forge bin/foreman /opt/agent-forge/bin/foreman
COPY --chown=forge:forge bin/status /opt/agent-forge/bin/status
COPY --chown=forge:forge roles/ /opt/agent-forge/roles/
COPY --chown=forge:forge prompts/ /opt/agent-forge/prompts/
COPY --chown=forge:forge etc/*.example.conf /opt/agent-forge/etc/

RUN chmod 755 /opt/agent-forge/bin/foreman /opt/agent-forge/bin/status

USER forge

EXPOSE 8171

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8171/api/status')"

CMD ["python3", "web/server.py"]
