FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV MCP_PORT=3010
EXPOSE 3010

CMD ["python", "server.py"]
