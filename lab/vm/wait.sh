#!/usr/bin/env bash
# Wait until nw-vm has booted and accepts SSH logins, then print the ssh command.
set -euo pipefail
VM_DIR="$(cd "$(dirname "$0")" && pwd)"
LAB_DIR="$(cd "$VM_DIR/.." && pwd)"
REPO_DIR="$(cd "$LAB_DIR/.." && pwd)"
KEYS="$VM_DIR/keys"
echo "Waiting for nw-vm to boot..."
state=unknown
for _ in $(seq 1 60); do
  state="$(docker exec nw-vm systemctl is-system-running 2>/dev/null || true)"
  case "$state" in running|degraded) break ;; esac
  sleep 2
done
echo "nw-vm systemd state: ${state:-unknown}"
ok=no
for _ in $(seq 1 30); do
  if ssh -i "$KEYS/id_ed25519" -p 2222 -o BatchMode=yes -o ConnectTimeout=3 \
       -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KEYS/known_hosts" \
       nightwatch@localhost true 2>/dev/null; then ok=yes; break; fi
  sleep 2
done
echo "nw-vm ssh: $ok"
rel="${KEYS#"$REPO_DIR"/}"
echo "SSH (run from the repository root):"
echo "  ssh -i $rel/id_ed25519 -p 2222 nightwatch@localhost"
echo "Strict host key check with the lab known_hosts file:"
echo "  ssh -i $rel/id_ed25519 -p 2222 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$rel/known_hosts nightwatch@localhost"
