# syntax=docker/dockerfile:1
ARG GITEA_BASE_IMAGE=docker.gitea.com/gitea:1.27.3
ARG TAILSCALE_IMAGE=tailscale/tailscale:v1.102.3@sha256:8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680
FROM ${TAILSCALE_IMAGE} AS tailscale
FROM ${GITEA_BASE_IMAGE}

USER root
RUN apk add --no-cache bash curl ca-certificates git git-lfs openssh-client \
      nodejs npm python3 ripgrep libgcc libstdc++ jq \
    && node -e 'if (Number(process.versions.node.split(".")[0]) < 24) process.exit(1)'

# Install outside the persistent agent home so rebuilding can update the CLIs.
WORKDIR /opt/harness
COPY package.json package-lock.json ./
RUN apk add --no-cache --virtual .harness-build-deps make g++ linux-headers \
    && npm ci --omit=dev --no-audit --no-fund \
    && npm_config_build_from_source=true npm rebuild node-pty \
    && ln -s /opt/harness/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -s /opt/harness/node_modules/.bin/dsh /usr/local/bin/dsh \
    && ln -s /opt/harness/node_modules/.bin/pnpm /usr/local/bin/pnpm \
    && npm cache clean --force \
    && apk del .harness-build-deps

RUN addgroup -g 1001 agent \
    && adduser -D -u 1001 -G agent -h /home/agent -s /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /home/agent /workspace \
    && chmod 700 /home/agent \
    && chmod 750 /workspace

ENV USE_BUILTIN_RIPGREP=0 DISABLE_AUTOUPDATER=1
COPY docker/entrypoint.sh /usr/local/bin/gitea-agent-entrypoint
COPY docker/check-harnesses.cjs /opt/harness/check-harnesses.cjs
RUN chmod 755 /usr/local/bin/gitea-agent-entrypoint \
    && claude --version \
    && dsh --version \
    && node /opt/harness/check-harnesses.cjs

COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY docker/tailscaled.run /etc/s6/tailscaled/run
RUN chmod 755 /etc/s6/tailscaled/run && tailscale version

WORKDIR /
ENTRYPOINT ["/usr/local/bin/gitea-agent-entrypoint"]
CMD ["/usr/bin/s6-svscan", "/etc/s6"]
