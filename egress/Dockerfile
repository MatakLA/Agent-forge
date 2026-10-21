# Egress proxy for workers: squid with a domain allowlist, running as non-root user "proxy".
FROM ubuntu@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944
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
