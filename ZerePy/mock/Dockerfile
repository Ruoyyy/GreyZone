FROM python:3.11-slim

RUN pip install --no-cache-dir fastapi uvicorn eth-account

WORKDIR /app
COPY twitter_mock.py .
COPY static ./static

CMD ["uvicorn", "twitter_mock:app", "--host", "0.0.0.0", "--port", "80"]
