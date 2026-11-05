FROM debian:bookworm-slim

# Lightweight lab image: only the agent binary + kubectl, no full gcloud suite.
# Build (repo root as context, run `make build` first to produce bin/kubectl-ai):
#   docker build -f images/threat-lab/Dockerfile -t kubectl-ai:lab .
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    curl -fsSL "https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    useradd -m -u 1000 agent

COPY --chmod=0755 bin/kubectl-ai /usr/local/bin/kubectl-ai

# Working directory: traces, sessions, and tool artifacts land here for easy mounting
RUN mkdir -p /home/agent/work && chown -R agent:agent /home/agent
USER agent
WORKDIR /home/agent/work

ENTRYPOINT ["/usr/local/bin/kubectl-ai"]
