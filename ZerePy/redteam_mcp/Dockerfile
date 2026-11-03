# RedTeam MCP: alice's Twitter account (streamable HTTP). Decoupled from the environment: reaches Twitter API v2 only via TWITTER_API_BASE + token
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

EXPOSE 8790
CMD ["python", "server.py"]
