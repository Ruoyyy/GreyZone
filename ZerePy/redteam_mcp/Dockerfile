# RedTeam MCP: alice's Twitter account (streamable HTTP). Decoupled from the environment: reaches Twitter API v2 only via TWITTER_API_BASE + token
FROM python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock
COPY server.py .

EXPOSE 8790
CMD ["python", "server.py"]
