# NightWatch fault lab

The lab gives NightWatch a small system with known faults. Use it to test and score the agent.

There are three labs:

- **Compose lab**: Docker Compose project `nightwatch-lab`. It includes Grafana, Loki and Promtail.
- **VM lab**: Linux host `nw-vm` with systemd, journald and SSH. It is part of the compose project.
- **Kubernetes lab**: kind cluster `nightwatch`, namespace `shop`.

Both labs use the same api image, `nightwatch-api:lab`.

## Requirements

- Docker Desktop must run.
- `kind` and `kubectl` must be installed.
- Ports 2222, 3000, 3100, 8080, 8081 and 9090 on localhost must be free.
- `ssh` and `ssh-keygen` must be installed.
- Docker Desktop must have approximately 2 GB of memory. Each new container has a memory limit.

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
| `nw-loki` | grafana/loki:3.4.2 | Log store. Single binary, filesystem storage, 6h retention. Host port 3100. Memory limit 200m. | nw-front |
| `nw-promtail` | grafana/promtail:3.4.2 | Sends the container logs and the `nw-vm` journal to Loki. Memory limit 100m. | nw-front |
| `nw-grafana` | grafana/grafana-oss:11.5.2 | Dashboards and alert rules. Host port 3000. Memory limit 200m. | nw-front |
| `nw-vm` | nightwatch-vm:lab (Debian 12) | Linux host with systemd, journald, sshd and `shop-worker.service`. Host port 2222. Memory limit 200m. | nw-front |

The api has these endpoints:

- `/` increments a counter in Redis.
- `/healthz` returns 200 if Redis answers. It returns 503 and the reason if Redis does not answer.
- `/metrics` gives Prometheus metrics.

Prometheus has three alert rules: `ApiDown`, `ApiHigh5xxRatio` (more than 5%), `ApiHighLatencyP95` (more than 500ms).

### Start the compose lab

1. Run `bash lab/compose/up.sh`. The script also builds and starts `nw-vm`.
2. Open http://localhost:8080. Make sure that you get status 200.
3. Open http://localhost:9090 to see the metrics and the alerts.
4. Open http://localhost:3000 to see Grafana.

### Grafana and Loki

| Item | Value |
|---|---|
| Grafana URL | http://localhost:3000 |
| Grafana user | `admin` |
| Grafana password | Environment variable `GF_SECURITY_ADMIN_PASSWORD`. The lab default is `nightwatch-lab`. |
| Loki URL | http://localhost:3100 |

Anonymous access to Grafana is disabled. Use basic auth for the API. A service account is not necessary.

The password is a lab value only. To use a different password, set `GF_SECURITY_ADMIN_PASSWORD` before you run `lab/compose/up.sh`. Grafana keeps its database in the container. When Compose recreates `nw-grafana`, the password comes from the variable again.

Grafana gets its configuration from files in `lab/compose/grafana/`:

- Data sources: `Prometheus` (uid `prometheus`) and `Loki` (uid `loki`).
- Dashboard `Shop overview` (uid `shop-overview`, folder `Shop`). It shows the request rate by status, the p95 latency, the 5xx ratio, the api process starts, the api error logs and the `shop-worker` journal.
- Alert rules in folder `NightWatch`, group `shop`. The group is evaluated every 10s.

| Rule | UID | Condition | Severity |
|---|---|---|---|
| `ShopHigh5xx` | `shop-high-5xx` | 5xx ratio of GET / is more than 5% for 1m (Prometheus). | P1 |
| `ShopHighLatency` | `shop-high-latency` | p95 latency of GET / is more than 0.5s for 1m (Prometheus). | P1 |
| `ShopErrorLogs` | `shop-error-logs` | `sum(count_over_time({service="api"} \|= "ERROR" [1m]))` is more than 20 for 1m (Loki). | P2 |

Loki labels:

| Label | Source | Example |
|---|---|---|
| `container` | Container name without the slash | `nw-api` |
| `service` | Compose service | `api` |
| `project` | Compose project | `nightwatch-lab` |
| `stream` | Docker log stream | `stdout`, `stderr` |
| `host` | Journal of the VM only | `nw-vm` |
| `unit` | systemd unit (VM journal) | `shop-worker.service` |
| `level` | Journal priority (VM journal) | `info`, `warning`, `error` |
| `syslog_identifier` | Journal identifier (VM journal) | `shop-worker` |
| `job` | VM journal only | `systemd-journal` |

Do these checks:

```sh
curl -u admin:nightwatch-lab http://localhost:3000/api/health
curl -u admin:nightwatch-lab http://localhost:3000/api/datasources
curl -u admin:nightwatch-lab http://localhost:3000/api/v1/provisioning/alert-rules
curl -u admin:nightwatch-lab http://localhost:3000/api/prometheus/grafana/api/v1/rules
curl -u admin:nightwatch-lab http://localhost:3000/api/prometheus/grafana/api/v1/alerts
curl -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={container="nw-api"}' --data-urlencode limit=5
curl -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={host="nw-vm", unit="shop-worker.service"}' --data-urlencode limit=5
```

Promtail reads only the last 15 minutes of each container log when it starts for the first time.

### Stop the compose lab

1. Run `lab/compose/down.sh`.

## VM lab

### Components

`nw-vm` is a container that operates as a small Linux server:

- systemd is PID 1. journald keeps the journal in `/var/log/journal`.
- `sshd` listens on port 22. The host port is 2222.
- `shop-worker.service` is the application. It reads `/etc/shop-worker/worker.conf`. Every 5 seconds it writes a heartbeat line to the journal. It also writes `state.log` and `checkpoint` in `/var/lib/shop-worker`.
- `/var/lib/shop-worker` is a separate 32 MB disk (tmpfs).
- The unit has `Restart=on-failure`. After 3 failed starts in 60 seconds, systemd stops it. The unit then stays in state `failed`.

The container must be privileged because systemd must manage cgroups. It uses a private cgroup namespace.

### SSH access

- User `nightwatch`. Login with a key only. Password login and root login are disabled.
- The user is in the groups `systemd-journal` and `adm`. The user can read the journal. The user cannot use sudo.
- `lab/vm/keys.sh` makes the keys in `lab/vm/keys/`. Git ignores this directory.
  - `id_ed25519`, `id_ed25519.pub`: the key pair of user `nightwatch`.
  - `ssh_host_ed25519_key`: the host key of `nw-vm`. It does not change when you rebuild the image.
  - `known_hosts`: the known hosts file for the lab.

Connect from the repository root:

```sh
ssh -i lab/vm/keys/id_ed25519 -p 2222 nightwatch@localhost
```

For scripts, use batch mode and the lab known hosts file:

```sh
ssh -i lab/vm/keys/id_ed25519 -p 2222 -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=lab/vm/keys/known_hosts \
  nightwatch@localhost 'systemctl is-active shop-worker; journalctl -u shop-worker -n 5 --no-pager'
```

### Start and stop the VM

1. Run `bash lab/vm/up.sh` to build and start only `nw-vm`. The script prints the SSH command.
2. Run `bash lab/vm/down.sh` to remove only `nw-vm`. The keys stay.

`lab/compose/up.sh` also starts `nw-vm`. `lab/compose/down.sh` also removes it.

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
| `vm-service-crash` | vm | `/etc/shop-worker/worker.conf` gets a value that is not correct. `shop-worker` exits at start with status 78. After 3 restarts the unit is `failed`. The journal shows the error lines. |
| `vm-disk-full` | vm | A file fills `/var/lib/shop-worker` on `nw-vm`. The unit stays `active`, but each state write fails with "No space left on device". The journal shows the error lines. |

The file `evals/scenarios.yaml` gives the symptom and the expected result for each fault.

### Inject a fault

1. Run `lab/faults/list.sh` to see the fault IDs.
2. Run `lab/faults/fault.sh <id> inject`.
3. Wait for the time in `setup_seconds` in `evals/scenarios.yaml`.

### Reset a fault

1. Run `lab/faults/fault.sh <id> reset`.
2. Run `lab/faults/fault.sh <id> status`. Make sure that the output is `not injected`.

### Reset all faults

1. Run `lab/reset_all.sh`. The script restores all labs to a healthy state.
2. Run `lab/faults/list.sh --status` to see the status of each fault.

## Rules and notes

- Inject one fault at a time. Each fault script is idempotent.
- Do not give the agent access to the `lab/` directory. The scripts tell the answer.
- The VM faults use `docker exec nw-vm` as root. The agent uses SSH as user `nightwatch`.
- The fault `redis-down` also makes the Grafana alerts `ShopHigh5xx` and `ShopErrorLogs` fire after approximately 70 seconds.
- Promtail has read access to the Docker socket. Do not give the agent access to `nw-promtail`.
- The faults `api-oom` and `bad-env` write `lab/compose/docker-compose.override.yml`. Docker Compose reads this file automatically. Their reset removes the setting.
- The fault `nginx-bad-upstream` changes `lab/compose/nginx/conf.d/default.conf`. Its reset restores the file.
- To repair `dns-break` by hand, include the alias: `docker network connect --alias redis nw-back nw-redis`. Without the alias, the name `redis` does not resolve.
- Docker Desktop with the containerd image store cannot always use `kind load` for multi-platform images. `lab/k8s/up.sh` then imports the image with `ctr`.
