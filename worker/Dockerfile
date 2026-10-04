# ==============================================================================
# Worker Container Image
# Multi-provider AI development environment:
# - Python 3 & pytest runtime
# - Git & Forgejo credential integration
# - D-Bus & gnome-keyring for headless CLI sessions
# - Antigravity CLI (agy) for Gemini / Claude / GPT models
# - Node.js LTS for Claude Code CLI and modern JS tooling
# ==============================================================================
FROM ubuntu@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78

ARG AGY_VERSION=1.2.11
ARG AGY_URL=https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.11-6016716732497920/linux-x64/cli_linux_x64.tar.gz
ARG AGY_SHA512=ca12c262343f29a2b87423d1ff1e4244989e936e37fe1f8e56056b0f91cd02f93f133321229ce35c86c2d84b977e919937a3fd9430cfd769a16d6b03ede25081

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq procps \
      python3 python3-pip python3-venv python3-pytest \
      dbus dbus-daemon gnome-keyring libsecret-tools \
 && rm -rf /var/lib/apt/lists/*

# Install agy outside HOME so named volumes never pin an outdated binary
RUN mkdir -p /opt/agy \
 && curl -fsSL -o /tmp/agy.tar.gz "$AGY_URL" \
 && echo "$AGY_SHA512  /tmp/agy.tar.gz" | sha512sum -c - \
 && tar -xzf /tmp/agy.tar.gz -C /opt/agy antigravity \
 && mv /opt/agy/antigravity /opt/agy/agy \
 && rm /tmp/agy.tar.gz \
 && chown -R root:root /opt/agy && chmod 755 /opt/agy /opt/agy/agy \
 && ln -s /opt/agy/agy /usr/local/bin/agy

RUN userdel -r ubuntu 2>/dev/null || true \
 && useradd --uid 1000 --create-home --shell /bin/bash worker

COPY agy-run /usr/local/bin/agy-run
COPY agent-run /usr/local/bin/agent-run
COPY git-credential-forgejo /usr/local/bin/git-credential-forgejo
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY gitconfig /etc/gitconfig
RUN chmod 755 /usr/local/bin/agy-run /usr/local/bin/agent-run /usr/local/bin/git-credential-forgejo /usr/local/bin/entrypoint.sh

ENV AGY_CLI_DISABLE_AUTO_UPDATE=true
USER worker
ENV HOME=/home/worker
WORKDIR /home/worker
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
