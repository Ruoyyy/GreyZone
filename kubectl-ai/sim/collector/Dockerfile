FROM python:3.12-alpine
COPY server.py /app/server.py
WORKDIR /app
VOLUME /log
EXPOSE 31337
CMD ["python", "server.py"]
