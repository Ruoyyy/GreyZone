# anvil + deterministic history seeding (seed.py). Every container start regenerates the same history
FROM ghcr.io/foundry-rs/foundry:latest AS foundry

FROM python:3.11-slim
RUN pip install --no-cache-dir eth-account
COPY --from=foundry /usr/local/bin/anvil /usr/local/bin/anvil

WORKDIR /chain
# Copy only the scripts; users.keys.json is mounted at runtime and never enters the image
COPY seed.py entrypoint.sh ./

ENTRYPOINT ["sh", "/chain/entrypoint.sh"]
