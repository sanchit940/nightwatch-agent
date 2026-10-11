#!/usr/bin/env bash
# Build and start nw-vm (Linux host with systemd, journald and sshd) in the compose project.
# lab/compose/up.sh also runs this part; use this script to (re)start only the VM.
set -euo pipefail
VM_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$VM_DIR/keys.sh"
(cd "$VM_DIR/../compose" && docker compose up -d --build --no-deps vm)
bash "$VM_DIR/wait.sh"
