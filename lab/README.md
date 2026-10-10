# NightWatch fault lab

The lab gives NightWatch a small system with known faults. Use it to test and score the agent.

There are two labs:

- **Compose lab**: Docker Compose project `nightwatch-lab`.
- **Kubernetes lab**: kind cluster `nightwatch`, namespace `shop`.

Both labs use the same api image, `nightwatch-api:lab`.

## Requirements

- Docker Desktop must run.
- `kind` and `kubectl` must be installed.
- Ports 8080, 8081 and 9090 on localhost must be free.

## Compose lab

### Components

| Container | Image | Function | Networks |
|---|---|---|---|
| `nw-web` | nginx:1.27-alpine | Reverse proxy to `api:8000`. Host port 8080. | nw-front |
| `nw-api` | nightwatch-api:lab | Counter API. Limits: 256m memory, 0.5 CPU, 64m tmpfs on `/data`. | nw-front, nw-back |
| `nw-redis` | redis:7-alpine | Counter store. | nw-back |
| `nw-toolbox` | python:3.12-alpine | Probe source (nslookup, wget, nc, python). | nw-front, nw-back |
| `nw-prometheus` | prom/prometheus:v2.55.1 | Scrapes `nw-api` every 5s. Host port 9090. | nw-front |
| `nw-loadgen` | alpine:3.20 | Sends about 5 requests per second to `http://web/`. | nw-front |

The api has these endpoints:

- `/` increments a counter in Redis.
- `/healthz` returns 200 if Redis answers. It returns 503 and the reason if Redis does not answer.
- `/metrics` gives Prometheus metrics.

Prometheus has three alert rules: `ApiDown`, `ApiHigh5xxRatio` (more than 5%), `ApiHighLatencyP95` (more than 500ms).

### Start the compose lab

1. Run `lab/compose/up.sh`.
2. Open http://localhost:8080. Make sure that you get status 200.
3. Open http://localhost:9090 to see the metrics and the alerts.

### Stop the compose lab

1. Run `lab/compose/down.sh`.

## Kubernetes lab

### Components

All objects are in namespace `shop`.

| Object | Function |
|---|---|
| `deployment/api`, `service/api` | 2 replicas of `nightwatch-api:lab`. Strategy `Recreate`. Readiness and liveness probes on `/healthz` port 8000. Service type NodePort 30080. |
| `deployment/redis`, `service/redis` | Counter store. |
| `deployment/toolbox` | Probe source (python:3.12-alpine). |
| `configmap/api-config` | Gives `REDIS_HOST` and `REDIS_PORT` to the api. |

The NodePort is mapped to http://localhost:8081.

### Start the Kubernetes lab

1. Start the compose lab first, or build the image with `docker build -t nightwatch-api:lab lab/compose/api`.
2. Run `lab/k8s/up.sh`. The script creates the cluster, loads the images and applies `lab/k8s/manifests/`.
3. Open http://localhost:8081. Make sure that you get status 200.

The kube context is `kind-nightwatch`.

### Stop the Kubernetes lab

1. Run `lab/k8s/down.sh`. The script deletes the cluster.

## Faults

| ID | Lab | Result |
|---|---|---|
| `redis-down` | compose | `nw-redis` stops. The web returns 503. |
| `nginx-bad-upstream` | compose | nginx proxies to `api:8001`. The web returns 502. |
| `api-oom` | compose | The api is recreated with `CACHE_WARM_MB=400`. The kernel kills it (OOM) again and again. Some requests fail with 502 or 504. |
| `api-cpu-hog` | compose | A CPU job runs in `nw-api`. CPU throttling occurs. p95 latency is near 1s. |
| `disk-full` | compose | A file fills `/data` in `nw-api`. Writes fail with ENOSPC. The web returns 500. |
| `dns-break` | compose | `nw-redis` is disconnected from `nw-back`. The name `redis` does not resolve. The web returns 503. |
| `bad-env` | compose | The api is recreated with `REDIS_HOST=redis-primary`. The web returns 503. |
| `k8s-bad-image` | k8s | `deployment/api` uses an image tag that does not exist. Pods are in `ImagePullBackOff`. |
| `k8s-bad-probe` | k8s | The readiness probe uses port 8001. Pods are not Ready. The Service has no ready endpoints. |
| `k8s-oom` | k8s | The api memory limit is 20Mi. Pods are `OOMKilled` and go into `CrashLoopBackOff`. |
| `k8s-pending` | k8s | The api has 3 replicas with a CPU request of 4. Pods stay `Pending`. |
| `k8s-redis-scaled-zero` | k8s | `deployment/redis` has 0 replicas. The api pods become unready and restart. |

The file `evals/scenarios.yaml` gives the symptom and the expected result for each fault.

### Inject a fault

1. Run `lab/faults/list.sh` to see the fault IDs.
2. Run `lab/faults/fault.sh <id> inject`.
3. Wait for the time in `setup_seconds` in `evals/scenarios.yaml`.

### Reset a fault

1. Run `lab/faults/fault.sh <id> reset`.
2. Run `lab/faults/fault.sh <id> status`. Make sure that the output is `not injected`.

### Reset all faults

1. Run `lab/reset_all.sh`. The script restores both labs to a healthy state.
2. Run `lab/faults/list.sh --status` to see the status of each fault.

## Rules and notes

- Inject one fault at a time. Each fault script is idempotent.
- Do not give the agent access to the `lab/` directory. The scripts tell the answer.
- The faults `api-oom` and `bad-env` write `lab/compose/docker-compose.override.yml`. Docker Compose reads this file automatically. Their reset removes the setting.
- The fault `nginx-bad-upstream` changes `lab/compose/nginx/conf.d/default.conf`. Its reset restores the file.
- To repair `dns-break` by hand, include the alias: `docker network connect --alias redis nw-back nw-redis`. Without the alias, the name `redis` does not resolve.
- Docker Desktop with the containerd image store cannot always use `kind load` for multi-platform images. `lab/k8s/up.sh` then imports the image with `ctr`.
