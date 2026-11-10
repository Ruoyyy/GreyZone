FROM ghcr.io/home-assistant/home-assistant:2026.10.1@sha256:130241f28d01fa80dfa3b44f7d89781c188d8642ecab9024f079f19f9f82baa0

# Preinstall the recorded HGA runtime, including versions selected under HA's pins.
COPY --from=hga requirements_runtime.lock /tmp/hga-requirements.lock
RUN uv pip install --system --no-cache --index-strategy unsafe-best-match --require-hashes \
      -r /tmp/hga-requirements.lock
