#!/usr/bin/env bash
# Fault: a large replay file fills the 32m data disk /var/lib/shop-worker on nw-vm.
# shop-worker stays "active", but every state write fails with ENOSPC
# (No space left on device) and it logs errors to the journal. No batch is committed.
set -euo pipefail
FAULT_ID=vm-disk-full
source "$(dirname "$0")/lib.sh"
DATA=/var/lib/shop-worker
FILL=$DATA/orders-replay.ndjson

data_use() { vm df "$DATA" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}'; }

do_inject() {
  require_vm
  vm sh -c "dd if=/dev/zero of=$FILL bs=1M >/dev/null 2>&1 || true"
  log "$DATA usage: $(data_use)%"
  sleep 6
}

do_reset() {
  require_vm
  vm rm -f "$FILL"
  if [ "$(vm_unit_state shop-worker.service)" != active ]; then
    vm systemctl reset-failed shop-worker.service || true
    vm systemctl restart shop-worker.service
  fi
  wait_vm_unit_state shop-worker.service active 30
  log "reset done: $DATA usage $(data_use)%"
}

do_status() {
  local use; use="$(data_use)"
  if [ "${use:-0}" -ge 95 ]; then echo "injected ($DATA ${use}% used)"; else echo "not injected ($DATA ${use:-?}% used)"; fi
}

fault_main "$@"
