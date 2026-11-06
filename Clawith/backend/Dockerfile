# ─── Backend Dockerfile (Optimized) ─────────────────────
# Multi-stage: deps → production
FROM python:3.12-slim AS deps

WORKDIR /app
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources; \
    else \
        sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info pango1.0-tools && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
ARG UV_VERSION=0.11.15
ARG CLAWITH_PIP_INDEX_URL
ARG CLAWITH_PIP_TRUSTED_HOST
RUN set --; \
    if [ -n "$CLAWITH_PIP_INDEX_URL" ]; then \
        set -- "$@" --index-url "$CLAWITH_PIP_INDEX_URL"; \
        if [ -n "$CLAWITH_PIP_TRUSTED_HOST" ]; then \
            set -- "$@" --trusted-host "$CLAWITH_PIP_TRUSTED_HOST"; \
        fi; \
    fi; \
    pip install --no-cache-dir "$@" "uv==${UV_VERSION}" && \
    UV_PYTHON_DOWNLOADS=never uv lock --check --offline && \
    uv export --frozen --no-dev --no-emit-project --format requirements.txt \
        --output-file /tmp/runtime-requirements.txt > /dev/null && \
    pip install --no-cache-dir "$@" --require-hashes -r /tmp/runtime-requirements.txt && \
    pip install --no-cache-dir "$@" --no-deps .

# ─── Production ─────────────────────────────────────────
FROM python:3.12-slim AS production

WORKDIR /app
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources; \
    else \
        sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl shadowsocks-libev gosu bubblewrap libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info pango1.0-tools fonts-wqy-microhei fonts-wqy-zenhei fonts-noto-cjk chromium && \
    fc-cache -fv && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=deps /usr/local/bin/ /usr/local/bin/

# Copy application code
COPY . .

RUN useradd --create-home clawith && \
    mkdir -p /data/agents && \
    chmod +x /app/entrypoint.sh && \
    chmod u+s /usr/bin/bwrap && \
    chown -R clawith:clawith /app /data

# Note: USER is removed to allow entrypoint.sh to fix permissions of mounted volumes
# at runtime. The entrypoint script will drop privileges to 'clawith' after fixing permissions.

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

EXPOSE 8000
# entrypoint.sh bootstraps Alembic and LangGraph checkpoints before `uvicorn`
ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
