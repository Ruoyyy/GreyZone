# ruoyu 的 Twitter 账号 MCP（streamable HTTP）。与环境解耦：只通过 TWITTER_API_BASE + token 访问 Twitter API v2
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

EXPOSE 8790
CMD ["python", "server.py"]
