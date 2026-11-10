FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

ARG DEBIAN_SNAPSHOT=20261104T000000Z
ARG KUBECTL_VERSION=v1.34.1
ARG KUBECTL_SHA256=7721f265e18709862655affba5343e85e1980639395d5754473dafaadcaa69e3

# Lightweight lab image: only the agent binary + kubectl, no full gcloud suite.
# Build bin/kubectl-ai first, then build this image from the benchmark root:
#   python3 scripts/build_images.py --dockerfile kubectl-ai/sim/victim/Dockerfile \
#     --context kubectl-ai --tag kubectl-ai:lab
RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
      -e "s|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|" \
      /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::Check-Valid-Until=false update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl && \
    printf '%s  /usr/local/bin/kubectl\n' "$KUBECTL_SHA256" | sha256sum -c - && \
    chmod +x /usr/local/bin/kubectl && \
    useradd -m -u 1000 agent

COPY --chmod=0755 bin/kubectl-ai /usr/local/bin/kubectl-ai

# Working directory: traces, sessions, and tool artifacts land here for easy mounting
RUN mkdir -p /home/agent/work && chown -R agent:agent /home/agent
USER agent
WORKDIR /home/agent/work

ENTRYPOINT ["/usr/local/bin/kubectl-ai"]
