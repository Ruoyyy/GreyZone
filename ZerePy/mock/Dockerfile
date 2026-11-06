FROM python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.lock

WORKDIR /app
COPY twitter_mock.py seed_tweets.json ./
COPY static ./static

CMD ["uvicorn", "twitter_mock:app", "--host", "0.0.0.0", "--port", "80"]
