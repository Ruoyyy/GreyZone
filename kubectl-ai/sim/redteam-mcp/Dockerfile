# Deployment recipe follows Clawith/redteam-mcp: fastmcp + HTTP transport + static key
FROM python:3.12.16-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

ARG DEBIAN_SNAPSHOT=20261104T000000Z
ARG KUBECTL_VERSION=v1.34.1
ARG KUBECTL_SHA256=7721f265e18709862655affba5343e85e1980639395d5754473dafaadcaa69e3

RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
      -e "s|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|" \
      /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::Check-Valid-Until=false update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
      -o /usr/local/bin/kubectl && \
    printf '%s  /usr/local/bin/kubectl\n' "$KUBECTL_SHA256" | sha256sum -c - && \
    chmod +x /usr/local/bin/kubectl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /app/requirements.lock
COPY server.py /app/server.py

WORKDIR /app
EXPOSE 18092
CMD ["python", "server.py", "--transport", "http", "--port", "18092"]
