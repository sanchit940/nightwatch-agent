# NightWatch: one image for any cloud. Configuration and secrets stay outside the image:
#   /config  policy.yaml, inventory.yaml, watch.yaml (and SSH keys, if any), mounted read-only
#   /data    audit database and Slack dry-run outbox (a volume)
#   env      your own LLM key and the other settings (deploy/.env.example)
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 nightwatch \
 && mkdir -p /config /data && chown nightwatch /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY nightwatch ./nightwatch
COPY knowledge ./knowledge
COPY deploy/config/ /config/
RUN pip install --no-cache-dir -e . && rm -rf /root/.cache

ENV NIGHTWATCH_CONFIG_DIR=/config \
    NIGHTWATCH_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
USER nightwatch
VOLUME ["/data"]
ENTRYPOINT ["nightwatch"]
CMD ["watch", "--approval", "slack"]
