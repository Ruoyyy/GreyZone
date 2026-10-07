# Build context: the repo root. Copy only what the package needs, so local files
# such as sim/.env (API keys) or agent transcripts can never end up in the image.
FROM python:3.11-slim
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir . && rm -rf /src
COPY sim/run_mcp.py /app/run_mcp.py
EXPOSE 8000
CMD ["python", "/app/run_mcp.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
