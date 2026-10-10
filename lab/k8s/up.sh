#!/usr/bin/env bash
# Create the kind cluster "nightwatch" (if missing), load images, apply manifests.
set -euo pipefail
cd "$(dirname "$0")"
CLUSTER=nightwatch
CTX="kind-$CLUSTER"
IMAGES=(nightwatch-api:lab redis:7-alpine python:3.12-alpine)

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config kind-config.yaml --wait 120s
else
  echo "kind cluster $CLUSTER already exists"
fi

# Build the api image if it is not present (same image as the compose lab).
docker image inspect nightwatch-api:lab >/dev/null 2>&1 || docker build -t nightwatch-api:lab ../compose/api

node="${CLUSTER}-control-plane"
arch="$(docker exec "$node" uname -m)"
case "$arch" in aarch64|arm64) platform=linux/arm64 ;; *) platform=linux/amd64 ;; esac
for img in "${IMAGES[@]}"; do
  docker image inspect "$img" >/dev/null 2>&1 || docker pull --platform "$platform" "$img"
  echo "loading $img into $CLUSTER"
  if ! kind load docker-image "$img" --name "$CLUSTER" >/dev/null 2>&1; then
    # Fallback for the containerd image store (multi-platform images): import one platform.
    docker save --platform "$platform" "$img" \
      | docker exec -i "$node" ctr -n k8s.io images import --snapshotter overlayfs - >/dev/null
  fi
done

kubectl --context "$CTX" apply -f manifests/
kubectl --context "$CTX" -n shop rollout status deployment/redis --timeout=180s
kubectl --context "$CTX" -n shop rollout status deployment/api --timeout=180s
kubectl --context "$CTX" -n shop rollout status deployment/toolbox --timeout=180s
kubectl --context "$CTX" -n shop get deploy,pods,svc
# Wait until the NodePort answers (endpoints take a few seconds to propagate).
for _ in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -m 2 -w '%{http_code}' http://localhost:8081/ || true)" = 200 ] && break
  sleep 1
done
echo "http://localhost:8081 -> $(curl -s -o /dev/null -m 2 -w '%{http_code}' http://localhost:8081/ || true)"
