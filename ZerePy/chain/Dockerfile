# anvil + deterministic history seeding (seed.py). Every container start regenerates the same history
FROM ghcr.io/foundry-rs/foundry:v1.8.7@sha256:6fec6dc8249fb0231144b159e4ee1d86e486e3dc8eccb09a25e5e16ce494236b AS foundry

FROM python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.lock
COPY --from=foundry /usr/local/bin/anvil /usr/local/bin/anvil

WORKDIR /chain
# Copy only the scripts; users.keys.json is mounted at runtime and never enters the image
COPY seed.py entrypoint.sh ./

ENTRYPOINT ["sh", "/chain/entrypoint.sh"]
