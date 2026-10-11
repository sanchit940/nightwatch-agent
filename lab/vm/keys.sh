#!/usr/bin/env bash
# Create the SSH keys for nw-vm in lab/vm/keys/ (idempotent). The directory is git-ignored.
#   id_ed25519, id_ed25519.pub   client key pair for user nightwatch
#   ssh_host_ed25519_key         host key of nw-vm (stable across rebuilds)
set -euo pipefail
KEYS="$(cd "$(dirname "$0")" && pwd)/keys"
mkdir -p "$KEYS"
chmod 700 "$KEYS"
if [ ! -f "$KEYS/id_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N '' -C nightwatch@nw-lab -f "$KEYS/id_ed25519"
fi
if [ ! -f "$KEYS/ssh_host_ed25519_key" ]; then
  ssh-keygen -q -t ed25519 -N '' -C root@nw-vm -f "$KEYS/ssh_host_ed25519_key"
  rm -f "$KEYS/known_hosts"
fi
touch "$KEYS/known_hosts"
