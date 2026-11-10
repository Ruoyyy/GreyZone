# Build context: the repo root. Copy only what the package needs, so local files
# such as sim/.env (API keys) or agent transcripts can never end up in the image.
FROM python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
COPY sim/requirements-mcp.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.lock && \
    pip install --no-cache-dir --no-deps . && rm -rf /src
COPY sim/run_mcp.py /app/run_mcp.py
EXPOSE 8000
CMD ["python", "/app/run_mcp.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
