#!/usr/bin/env bash
# Stop and remove the Docker Compose lab (containers and networks).
set -euo pipefail
cd "$(dirname "$0")"
docker compose down --remove-orphans
