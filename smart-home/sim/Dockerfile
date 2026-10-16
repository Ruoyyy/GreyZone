FROM ghcr.io/home-assistant/home-assistant:2026.10.1

# Preinstall HGA's runtime deps (from manifest.json) under HA's own pins, so the
# container doesn't pip-install them on every fresh start.
COPY --from=hga requirements_runtime_manifest.txt /tmp/hga-requirements.txt
RUN uv pip install --system --no-cache --index-strategy unsafe-best-match \
      -c /usr/src/homeassistant/homeassistant/package_constraints.txt \
      -r /tmp/hga-requirements.txt
