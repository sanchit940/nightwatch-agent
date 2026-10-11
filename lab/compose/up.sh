#!/usr/bin/env bash
# Build the lab images and start the Docker Compose lab (shop, observability stack and nw-vm).
set -euo pipefail
cd "$(dirname "$0")"
bash ../vm/keys.sh
docker compose build api vm
docker compose up -d --remove-orphans
echo "Waiting for nw-api to become healthy..."
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' nw-api 2>/dev/null || echo missing)
  [ "$state" = healthy ] && break
  sleep 2
done
docker compose ps
curl -fsS -o /dev/null -w 'http://localhost:8080 -> %{http_code}\n' http://localhost:8080/ || true
echo "Waiting for Grafana..."
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null http://localhost:3000/api/health 2>/dev/null && break
  sleep 2
done
curl -fsS -o /dev/null -w 'http://localhost:3000 (Grafana) -> %{http_code}\n' http://localhost:3000/api/health || true
echo "Waiting for Loki..."
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null http://localhost:3100/ready 2>/dev/null && break
  sleep 2
done
curl -fsS -o /dev/null -w 'http://localhost:3100 (Loki) -> %{http_code}\n' http://localhost:3100/ready || true
bash ../vm/wait.sh
