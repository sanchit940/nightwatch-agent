#!/usr/bin/env bash
# Build the api image and start the Docker Compose lab.
set -euo pipefail
cd "$(dirname "$0")"
docker compose build api
docker compose up -d --remove-orphans
echo "Waiting for nw-api to become healthy..."
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' nw-api 2>/dev/null || echo missing)
  [ "$state" = healthy ] && break
  sleep 2
done
docker compose ps
curl -fsS -o /dev/null -w 'http://localhost:8080 -> %{http_code}\n' http://localhost:8080/ || true
