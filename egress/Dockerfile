# Egress proxy for workers: squid with a domain allowlist, running as non-root user "proxy".
FROM ubuntu@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends squid \
 && rm -rf /var/lib/apt/lists/* \
 && squid --version | head -1
COPY squid.discovery.conf squid.enforce.conf /etc/squid/
COPY entrypoint.sh /usr/local/bin/egress-entrypoint
RUN chmod 755 /usr/local/bin/egress-entrypoint && chmod 644 /etc/squid/squid.*.conf
USER proxy
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/3128'
ENTRYPOINT ["/usr/local/bin/egress-entrypoint"]
