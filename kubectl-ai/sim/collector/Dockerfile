FROM python:3.12.16-alpine3.24@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8
COPY server.py /app/server.py
WORKDIR /app
VOLUME /log
EXPOSE 31337
CMD ["python", "server.py"]
