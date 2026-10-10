#!/usr/bin/env bash
# Fault: a runaway CPU-bound job runs inside nw-api (16 worker processes). The container
# hits its 0.5 CPU quota, gets throttled, and request latency rises.
set -euo pipefail
FAULT_ID=api-cpu-hog
source "$(dirname "$0")/lib.sh"

# The job looks like a background re-index task started by hand in the container.
JOB='import hashlib, multiprocessing as mp
def reindex(shard):
    h = hashlib.sha256()
    while True:
        h.update(b"shard-%d" % shard)
if __name__ == "__main__":
    workers = [mp.Process(target=reindex, args=(i,)) for i in range(16)]
    [w.start() for w in workers]
    [w.join() for w in workers]'

# Python snippet that finds (and optionally kills) the job processes via /proc.
FINDER='import os, signal, sys
marker = "def " + "reindex("
me = os.getpid(); found = []
for pid in os.listdir("/proc"):
    if not pid.isdigit() or int(pid) == me:
        continue
    try:
        cmd = open("/proc/%s/cmdline" % pid, "rb").read().decode(errors="ignore")
    except OSError:
        continue
    if marker in cmd:
        found.append(int(pid))
if len(sys.argv) > 1 and sys.argv[1] == "kill":
    for p in found:
        try: os.kill(p, signal.SIGKILL)
        except OSError: pass
print(len(found))'

job_count() { docker exec nw-api python -c "$FINDER" 2>/dev/null || echo 0; }

do_inject() {
  require_container nw-api
  container_running nw-api || die "nw-api is not running"
  if [ "$(job_count)" -gt 0 ]; then log "job already running"; return 0; fi
  docker exec -d nw-api python -c "$JOB"
  sleep 1
  log "started CPU job in nw-api ($(job_count) processes)"
}

do_reset() {
  require_container nw-api
  if container_running nw-api; then docker exec nw-api python -c "$FINDER" kill >/dev/null; fi
  sleep 1
  [ "$(job_count)" -eq 0 ] || die "CPU job still running"
  wait_http_ok http://localhost:8080/ 30
  log "reset done: CPU job killed"
}

do_status() {
  local n; n="$(job_count)"
  if [ "$n" -gt 0 ]; then echo "injected ($n job processes in nw-api)"; else echo "not injected"; fi
}

fault_main "$@"
