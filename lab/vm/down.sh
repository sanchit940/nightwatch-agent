#!/usr/bin/env bash
# Stop and remove nw-vm only. The SSH keys in lab/vm/keys/ stay.
set -euo pipefail
cd "$(dirname "$0")/../compose"
docker compose rm -s -f vm
