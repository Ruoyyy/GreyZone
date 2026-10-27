FROM debian:bookworm-slim

# 轻量实验镜像：只含 agent 二进制 + kubectl，不含 gcloud 全家桶
# 构建（仓库根目录为上下文，先 make build 产出 bin/kubectl-ai）:
#   docker build -f images/threat-lab/Dockerfile -t kubectl-ai:lab .
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    curl -fsSL "https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    useradd -m -u 1000 agent

COPY --chmod=0755 bin/kubectl-ai /usr/local/bin/kubectl-ai

# 工作目录：trace、会话、工具产物都落在这里，方便挂载出来
RUN mkdir -p /home/agent/work && chown -R agent:agent /home/agent
USER agent
WORKDIR /home/agent/work

ENTRYPOINT ["/usr/local/bin/kubectl-ai"]
