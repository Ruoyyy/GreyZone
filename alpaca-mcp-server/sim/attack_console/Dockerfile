FROM python:3.11-slim
RUN pip install --no-cache-dir fastmcp==3.4.7
WORKDIR /srv
COPY server.py ./
EXPOSE 8000
CMD ["python", "server.py"]
