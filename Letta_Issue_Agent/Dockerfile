# Letta Code runner; jobs run inside the container, with no Docker socket mounted.
# Pin the original runner v3.4.2 image used by this project.
FROM gitea/runner@sha256:50c352f0506b1878be93f2e9e97353cd6261cd2e7f7bfe70fcd968d00874f9f9

USER root

RUN apk add --no-cache ca-certificates curl git build-base python3 nodejs npm unzip jq \
    && rm -rf /var/cache/apk/*

# Alpine needs the musl build.
ARG BUN_VERSION=1.2.11
ARG TARGETARCH
RUN case "${TARGETARCH}" in \
      amd64) BUN_ARCH=x64 ;; \
      arm64) BUN_ARCH=aarch64 ;; \
      *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && BUN_DIR="bun-linux-${BUN_ARCH}-musl" \
    && curl -fsSL -o /tmp/bun.zip "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/${BUN_DIR}.zip" \
    && unzip -o -q /tmp/bun.zip -d /opt \
    && mv "/opt/${BUN_DIR}/bun" /usr/local/bin/bun \
    && rm -rf "/opt/${BUN_DIR}" /tmp/bun.zip \
    && bun --revision

# Pin the CLI version and disable auto-update; rebuild the image if it breaks.
ARG LETTA_CODE_VERSION=0.31.14
ENV DISABLE_AUTOUPDATER=1
RUN npm install -g "@letta-ai/letta-code@${LETTA_CODE_VERSION}" \
    && letta --version \
    && rm -rf /root/.cache/node-gyp /root/.npm

RUN mkdir -p /home/runner/letta-state

COPY entrypoint.sh /entrypoint.sh
COPY oracle/ /oracle/
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
