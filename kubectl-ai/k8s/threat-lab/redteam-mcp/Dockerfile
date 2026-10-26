# 参照 Clawith/mcp-bridge 的部署配方：fastmcp + http 传输 + 静态钥匙
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL "https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl" \
      -o /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY server.py /app/server.py

WORKDIR /app
EXPOSE 18092
CMD ["python", "server.py", "--transport", "http", "--port", "18092"]
