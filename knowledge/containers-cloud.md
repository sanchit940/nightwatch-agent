# Containers and Cloud: Limits, Neighbors, Host vs Container

Source: SP = Gregg, *Systems Performance* 2e, Chapter 11. `[ops]` = general Linux/Kubernetes practice, not from SP.
SP describes cgroup v1. v2 file names are marked `[ops v2]`.

## 1. Key facts

- The Linux kernel has no "container" object. A container is a set of namespaces plus cgroups. (SP §11.3.1)
- Namespaces control what a process can see: pid, mnt, net, uts, ipc, user, cgroup, time. (SP §11.3.1, Table 11.1)
- cgroups control how much a process can use: cpu, cpuset, memory, blkio/io, pids, net_cls/net_prio. (SP §11.3.1, Table 11.2)
- All containers on a host share one kernel. They contend for kernel locks, caches, and queues. (SP §11.3)
- Container CPU and memory run at bare-metal speed. The main cost is contention with other tenants. (SP §11.3.2)
- Extra I/O layers can add small overhead: overlayfs for files, bridge and iptables for network. (SP §11.3.2)
- Limits are reached before physical limits. Check limits **first**. (SP §11.3.4.4)
- Kubernetes (as of SP) does not limit block I/O. Disk contention between pods is possible. (SP §11.1.6)

## 2. Resource controls — SP §11.3.3, Table 11.3

| Resource | Priority (soft) | Limit (hard) | Effect at the limit |
|----------|-----------------|--------------|---------------------|
| CPU | CFS shares (k8s requests) | CFS bandwidth quota (k8s limits); cpusets | Throttling: threads wait until the next period. Latency rises. |
| Memory | soft limit | `memory.max` / `memory.limit_in_bytes` | Reclaim, then swap (if set), then OOM kill inside the cgroup. |
| Swap | — | memory+swap limit | OOM kill sooner. |
| FS capacity | — | FS quotas | ENOSPC. |
| Page cache | — | counted in the memory cgroup | Cache eviction counts against the container. |
| Disk I/O | blkio/io weight | IOPS and bytes/s throttles | I/O latency rises at the throttle rate. |
| Network | net_prio | qdiscs (for example tbf for k8s bandwidth) | Network latency rises; drops. |
| Tasks | — | `pids.max` `[ops]` | fork/clone fails with EAGAIN. |

CPU shares trap (SP §11.3.3): a container can "burst" into idle host CPU. Tests on an idle host look fast.
When neighbors become busy, the container gets only its share. A 10x slowdown can be normal behavior.
Ask: did the container lose burst capacity because neighbors got busier?

## 3. Which tools lie inside a container — SP §11.3.4.1, Table 11.6

| Tool run in container | Shows |
|-----------------------|-------|
| `uptime`, `mpstat`, `vmstat`, `free`, `iostat` | **Host** values, including other tenants. |
| `top` | Header mixes host and container values. Process list shows container only. |
| `ps`, `pidstat`, `pidstat -d` | Container processes only. |
| `sar -n DEV,TCP`, `tcpdump` | Container network namespace only. |
| `dmesg`, `perf`, BPF tools | Usually fail (no permission). |

Rule: inside a container, never conclude "headroom" or "busy" from host-wide tools. Read the cgroup files. (SP §11.3.4)
Example: `iostat` showed a busy disk from an idle container. The I/O was from other tenants. (SP §11.3.4.3)

## 4. Read container metrics (read-only)

1. Find the cgroup: `cat /proc/<PID>/cgroup`. Inside a container, `/sys/fs/cgroup/` is usually its own cgroup. `[ops]`
2. CPU limit: v2 `cpu.max` (quota period) `[ops v2]`; v1 `cpu.cfs_quota_us`, `cpu.cfs_period_us`.
3. CPU throttling: `cpu.stat` → `nr_periods`, `nr_throttled`, `throttled_usec` (v1 `throttled_time` in ns). (SP §11.3.4.2)
4. CPU use: v2 `cpu.stat usage_usec` `[ops v2]`; v1 `cpuacct.usage`, `cpuacct.usage_percpu`. (SP §11.3.4.2)
5. Memory: v2 `memory.current`, `memory.max`, `memory.events` (`oom_kill`, `max`), `memory.stat` `[ops v2]`; v1 `memory.usage_in_bytes`, `memory.limit_in_bytes`, `memory.failcnt`.
6. Pressure: `cpu.pressure`, `memory.pressure`, `io.pressure` per cgroup v2. (SP §7.5.2)
7. Disk: v2 `io.stat`, `io.max` `[ops v2]`; v1 `blkio.throttle.io_serviced`, `blkio.throttle.io_service_bytes`. (SP §11.3.4.3)
8. Tasks: `pids.current` vs `pids.max`. `[ops]`
9. Read counters twice, 10 s apart. Compute rates. (SP §11.3.4.3)
10. Platform views: `docker stats --no-stream`, `kubectl top pods`, `kubectl top nodes`, `systemd-cgtop -n 1`. (SP §11.3.4.2)
11. Pod events: `kubectl describe pod <p>`, `kubectl get events --sort-by=.lastTimestamp`. Look for `OOMKilled`, exit 137, `Evicted`, restarts. `[ops]`
12. From the host, map PIDs: `grep NSpid /proc/<hostPID>/status` shows host PID and container PID. (SP §11.3.4.2)

## 5. CPU throttling decision tree — based on SP §11.3.4.2, Fig. 11.13

Inputs: cgroup throttled time, non-voluntary context switches (`/proc/<PID>/status`), host idle CPU (`mpstat`), cpuset CPU busy, activity of other tenants.
1. Throttled time increases? → **Bandwidth (quota) throttled.** Container hit its CPU limit.
2. Else, non-voluntary context switches not increasing? → **Not CPU throttled.** Look at I/O, locks, or the app.
3. Else, host has idle CPU?
   - Yes, and cpuset CPUs are 100% busy → **cpuset throttled.** Its assigned CPUs are full.
   - Yes, and cpuset not full → not throttled by the system. Check app threads and locks.
4. Else (host CPUs all busy):
   - All other tenants idle → **physical CPU limit.** The container alone fills the host.
   - Other tenants busy → **share throttled.** Neighbors take their share; burst is gone.
Use the same idea for memory, disk, and network: compare usage with the limit, then with the host. (SP §11.3.4.2)

## 6. Container-limit problem vs host problem

| Evidence | Container-limit problem | Host / neighbor problem |
|----------|-------------------------|-------------------------|
| CPU | `nr_throttled` rising; usage ≈ quota; host has idle CPU | No throttling; host CPUs all busy; other cgroups high in `systemd-cgtop` |
| Memory | `memory.current` ≈ `memory.max`; cgroup `oom_kill`; host has free memory (SP §7.3.2) | Host `available` low; host swap or page scanning; global OOM in host `dmesg` |
| Disk | I/O rate flat at the `io.max`/blkio throttle; host disk not busy | Host `iostat` busy from other tenants; host `await` high |
| Network | Throughput flat at a known cap; latency up at the cap | Host interface near line rate; drops on host interface |
| Pressure | Cgroup PSI high, host PSI low | Host PSI high for many cgroups |
| Scope | Only this container slow | Many containers on the same node slow at the same time |
Procedure:
1. Check the container's own limits and counters first. (SP §11.3.4.4)
2. Compare the slow pod with healthy replicas of the same app on other nodes. `[ops]`
3. If replicas on other nodes are fine, suspect the node: neighbors or host faults.
4. If all replicas are slow, suspect the app, its config, its load, or a shared dependency.
5. Without host access, infer contention from rising I/O and scheduler latency at steady load. (SP §11.2.4.3)

## 7. Noisy neighbors — SP §11.1.5, §11.2.2, §11.3.2

Signs of contention from other tenants:
- CPU `%steal` in a VM is high. Hypervisor or neighbors take CPU time. (SP §11.2.4.2)
- Latency rises while your own load is steady.
- Lower CPU cache and TLB hit rates; more interrupts from neighbor I/O. (SP §11.3.2)
- Kernel contention: example of `lstat` slowing because of another container's dentry cache use. (SP §11.3.2)
- Disk latency outliers that are not explained by I/O size or pattern. (SP §11.2.4.3)
Rules:
- Do not blame a neighbor without data. People tend to blame what they cannot see. (SP §11.3.4.3)
- Report a neighbor as cause only with host-level per-cgroup data, or with a clear inference chain.

## 8. Hardware VMs (cloud instances) — SP §11.2

- Guests see virtual devices. `%util` of a virtual disk says little. Use latency (`await`). (SP §11.2.4.2)
- `st` in `vmstat` = CPU time not given to the guest: other tenants, hypervisor, or instance CPU caps. (SP §11.2.4.2)
- Cloud network and network-attached disk I/O are often throttled to quotas outside the VM. (SP §11.2.3)
- Balloon drivers can take guest memory. Check `dmesg | grep -i balloon`. (SP §11.2.3)
- Network storage (block, file, object) is shared. Its latency is less predictable than local disk. (SP §11.1.4)

## 9. Cloud operations

- Auto scaling can hide a problem. A DoS or a regression can add instances and cost. Check that growth matches real demand. (SP §11.1.3)
- CPU bursting into idle capacity can stop when neighbors get busy. Performance then drops to the guaranteed share. (SP §11.1.3, §11.3.3)
- Cloud credit-based burst instances can run out of credits. Check the provider's credit metric. `[ops]`
- Kubernetes adds network components (CNI, kube-proxy). Large iptables rule sets add CPU per packet. (SP §11.1.6, §11.3.2)
