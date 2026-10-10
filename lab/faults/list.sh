#!/usr/bin/env bash
# List the faults. Add --status to show whether each fault is injected now.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
FAULTS=(
  "redis-down|compose|Stop nw-redis"
  "nginx-bad-upstream|compose|nginx proxies to api:8001 (wrong port)"
  "api-oom|compose|api cache warm-up exceeds the 256m memory limit (OOM kill loop)"
  "api-cpu-hog|compose|CPU-bound job inside nw-api (CPU throttling, high latency)"
  "disk-full|compose|Fill the 64m /data tmpfs of nw-api (ENOSPC)"
  "dns-break|compose|Disconnect nw-redis from nw-back (name redis does not resolve)"
  "bad-env|compose|Recreate nw-api with REDIS_HOST=redis-primary"
  "k8s-bad-image|k8s|deployment/api image tag does not exist (ImagePullBackOff)"
  "k8s-bad-probe|k8s|readiness probe on wrong port (no Ready pods, no endpoints)"
  "k8s-oom|k8s|api memory limit 20Mi (OOMKilled, CrashLoopBackOff)"
  "k8s-pending|k8s|api CPU request 4 x 3 replicas (Pending, FailedScheduling)"
  "k8s-redis-scaled-zero|k8s|deployment/redis scaled to 0"
)
printf '%-24s %-8s %s\n' ID LAB DESCRIPTION
for f in "${FAULTS[@]}"; do
  IFS='|' read -r id lab desc <<<"$f"
  if [ "${1:-}" = "--status" ]; then
    st="$("$DIR/$id.sh" status 2>/dev/null || echo unknown)"
    printf '%-24s %-8s %s [%s]\n' "$id" "$lab" "$desc" "$st"
  else
    printf '%-24s %-8s %s\n' "$id" "$lab" "$desc"
  fi
done
