# Letta Code runner；job 在容器内执行，不挂载 Docker socket。
FROM gitea/runner:latest

USER root

RUN apk add --no-cache ca-certificates curl git build-base python3 nodejs npm unzip jq \
    && rm -rf /var/cache/apk/*

# Alpine 需要 musl 构建。
ARG BUN_VERSION=1.2.11
RUN curl -fsSL -o /tmp/bun.zip "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-x64-musl.zip" \
    && unzip -o -q /tmp/bun.zip -d /opt \
    && mv /opt/bun-linux-x64-musl/bun /usr/local/bin/bun \
    && rm -rf /opt/bun-linux-x64-musl /tmp/bun.zip \
    && bun --revision

# 固定 CLI 版本并关闭自动更新；损坏时重建镜像。
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
