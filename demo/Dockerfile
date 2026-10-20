# ==============================================================================
# Agent-Forge Demo Dashboard Container Image
# Runs the Agent-Forge web dashboard and admin console in demo mode.
# Python standard library only; no node, no worker image, no docker-in-docker,
# and no egress proxy service.
# ==============================================================================
FROM python:3.12-slim

ENV DEMO_MODE=1 \
    BIND_PORT=8171 \
    BIND_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/bin:${PATH}" \
    PYTHONPATH="/app/bin/lib:/app/web"

# Run as non-root user 'forge' (UID 1000) for safe local evaluation
RUN groupadd -g 1000 forge \
 && useradd -u 1000 -g forge -m -s /bin/bash forge \
 && mkdir -p /app \
 && chown -R forge:forge /app

WORKDIR /app

# Copy repository into /app
COPY --chown=forge:forge . /app

RUN chmod 755 /app/bin/foreman /app/bin/status 2>/dev/null || true

USER forge

EXPOSE 8171

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8171/api/status')"

CMD ["sh", "-c", "python3 tools/demo_state.py /app && exec python3 web/server.py"]
