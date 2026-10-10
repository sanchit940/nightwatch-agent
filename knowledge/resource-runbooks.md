# Resource Runbooks (read-only diagnosis)

Source: SP = Gregg, *Systems Performance* 2e. `[ops]` = general Linux practice, not from SP.
Use after the 60-second checklist (methodology.md §9). Give each command a count or timeout.
For each step: state the hypothesis, run the command, record the value, accept or reject.

## 1. CPU — SP §6.5, §6.6

Order from SP §6.5: monitoring, USE, profiling, then static checks.
1. Check limits first. In a container, read `cpu.stat` throttling counters (see containers-cloud.md). (SP §6.5.2)
2. Errors: `dmesg -T | grep -iE 'mce|thrott'`. Check that all CPUs are online. (SP §6.5.2)
3. Saturation: `vmstat 1 5` (`r` vs CPU count) and `cat /proc/pressure/cpu`. (SP §6.6.1)
4. Balance: `mpstat -P ALL 1 5`. One CPU at 100% means one hot thread. (SP §6.5.1)
5. Steal: `%steal` in `mpstat`. High steal means a VM neighbor or an instance CPU cap. (SP §11.2.4.2)
6. Who: `pidstat 1 5` and `pidstat -t -p <PID> 1 5` (per thread). Split user vs system time. (SP §6.5.3)
7. Why (kernel): high `%sys` → check syscall and interrupt rates: `vmstat` `in`, `cs`; `cat /proc/interrupts`. (SP §6.5.3)
8. Context switches: `pidstat -w 1 5`. High `nvcswch/s` = CPU contention or throttling. High `cswch/s` = blocking on I/O or locks. `[ops]`
9. Clock: `grep MHz /proc/cpuinfo`. Low clock can mean power or thermal limits. (SP §6.5.1)
10. Optional, needs root: `perf top` or a 30 s `perf record -F 49 -a -g` profile → flame graph. (SP §6.5.4)

Key metrics: per-CPU utilization, run-queue length, PSI cpu, `%steal`, `nr_throttled`, user/sys ratio.
Common root causes:
- More load than CPUs (load problem). Check request rate. (SP §2.3.8)
- Single-threaded code or one hot lock (architecture problem). (SP §2.3.8)
- Runaway loop or retry storm: unnecessary work. (SP §2.5.11)
- CPU quota (cgroup or instance type) reached before physical CPUs are busy. (SP §6.5.2)
- Noisy neighbor or hypervisor steal. (SP §11.2.2)
- Batch work (backup, compression, cron, GC) at peak time. (SP §2.5.11)
- Regression after deploy: same load, more CPU per request. (SP §1.11.2)

## 2. Memory (OOM, swap) — SP §7.4, §7.5

Order from SP §7.4: monitoring, USE, characterize usage.
1. Errors: `dmesg -T | grep -iE 'out of memory|killed process'`. Record victim PID, RSS, and cgroup. (SP §7.4.1)
2. Container? Compare `memory.current` with `memory.max`. Read `memory.events`. The host can have free memory while a cgroup is at its limit. (SP §7.3.2, §7.4.2)
3. Saturation: `vmstat 1 5` (`si`, `so`), `sar -B 1 5` (`pgscank`, `pgscand`), `cat /proc/pressure/memory`. (SP §7.4.1)
4. Utilization: `free -m`. Use `available`, not `free`. Page cache is reclaimable. (SP §7.2.6)
5. Who: `ps aux --sort=-rss | head`. Check each process RSS against its own config limit (for example a JVM heap). (SP §7.4.3)
6. Trend: sample RSS of the top process several times. Steady growth = leak or growth. (SP §7.4.6)
7. Kernel memory: `slabtop -o -s c | head`; `grep -E 'Slab|SUnreclaim|Shmem' /proc/meminfo`. (SP §7.4.3)
8. Swap: `swapon --show`. No swap means OOM kills come sooner, with no slow phase. (SP §7.3.2)
9. Per process detail: `pmap -x <PID> | tail -1`, `grep -E 'VmRSS|VmSwap' /proc/<PID>/status`. (SP §7.5)

Key metrics: available memory, swap rate, page scan rate, PSI memory, OOM events, cgroup usage vs limit.
Common root causes:
- Memory leak (software bug) vs memory growth (config or design). Ask: is it supposed to do that? (SP §7.4.6)
- Cache warm-up looks like a leak. Check app cache configuration. (SP §7.4.6)
- Container memory limit too low for the working set. (SP §11.3.3)
- App cache sized for a larger host after migration. (SP §7.4.3)
- New process takes memory from the page cache. Disk reads then rise. (SP §1.11.1)
- Kernel slab growth (for example dentry cache from many files). (SP §7.4.3)
Notes: an OOM kill is a symptom. Find what grew and when. Correlate with deploys, then test.

## 3. File systems and disks — SP §8.5, §9.5

Capacity and performance are different problems. Check capacity first: it is fast.
Capacity:
1. `df -h`: space > ~90% is a risk. Writes fail at 100% (ENOSPC). (SP App. A)
2. `df -i`: inode use near 100% gives ENOSPC with free space. Cause: many small files. `[ops]`
3. `grep -w ro /proc/mounts` and `dmesg -T | grep -iE 'read-only|fs error'`. A FS error can remount the FS read-only. `[ops]`
4. Space not freed after delete: a process holds the deleted file open. Optional root: `lsof +L1`. `[ops]`
5. Containers: check FS quotas and the writable layer size. (SP §11.3.3)
Performance (SP §9.5 order: USE, monitoring, workload, latency):
6. Errors: `dmesg -T | grep -iE 'i/o error|reset|timeout'`; `/sys/block/<dev>/device/ioerr_cnt`. (SP §9.5.2)
7. USE: `iostat -sxz 1 5`. Look at `%util`, `aqu-sz`, `await`. (SP §9.6.1)
8. Pressure: `cat /proc/pressure/io`. (SP §9.6.3)
9. Who: `pidstat -d 1 5`; optional root `iotop -o -b -n 3`. (SP §9.5.1)
10. Workload: IOPS, KB/s, read/write ratio, `areq-sz` (≤ 8 KB suggests random I/O). (SP §9.5.4, §9.6.1)
11. Is the app waiting? Compare app latency with disk `await`. If the FS takes 1% of request time, look elsewhere. (SP §8.5.2)
12. Cache: low memory means fewer page cache hits and more disk reads. Check `free -m` buff/cache trend. (SP §1.11.1, §8.5.3)
13. Optional root: `biolatency`, `biosnoop`, `ext4slower` (BCC) for latency distribution and outliers. (SP §9.5.1)

Key metrics: `await`, `aqu-sz`, `%util`, IOPS, throughput, PSI io, error counters, `df` space and inodes.
Common root causes:
- Disk full or inodes full (logs, temp files, many small files).
- Too much load: backup, compaction, log rotation, batch job at peak. (SP §2.9.1)
- Page cache shrank (memory pressure), so reads go to disk. (SP §1.11.1)
- Cloud volume IOPS or throughput quota reached. Latency rises, `%util` may look normal. (SP §11.1.4, §11.2.3)
- RAID rebuild or failed disk in a pool. (SP §8.4, §2.5.17)
- Noisy neighbor on shared storage. (SP §11.1.5)
- Logical I/O differs from physical I/O: write-back bursts, prefetch, metadata. (SP §8.3.12)

## 4. Network — SP §10.5, §10.6

Order from SP §10.5: monitoring, USE, static checks, workload characterization.
1. Errors: `ip -s link`; `sar -n EDEV 1 5`. Rising errors, drops, overruns. (SP §10.5.1)
2. Utilization: `sar -n DEV 1 5` vs link speed (`/sys/class/net/<if>/speed`). Also check cloud bandwidth caps. (SP §10.5.2)
3. TCP health: `sar -n TCP,ETCP 1 5`; `nstat -s`. Compute RetransSegs / OutSegs. (SP §10.6.4, §10.6.6)
4. Resets: `TcpEstabResets`, `TcpOutRsts`, `TcpAttemptFails` rising. Find the peer with `ss -tan state syn-sent`. `[ops]`
5. Listen backlog: `netstat -s | grep -iE 'overflow|SYNs to LISTEN'`; `ss -ltn` Recv-Q. Backlog drops and SYN retransmits mean host overload. (SP §10.3.7)
6. Sockets: `ss -tiepm` for key sockets. Read `rtt`, `retrans`, `cwnd`, `app_limited`. (SP §10.6.1)
7. Connections: `ss -s`. Many TIME_WAIT with a high connection rate can exhaust ephemeral ports. (SP §10.5.7)
8. Buffers: `netstat -s | grep -iE 'pruned|collapsed'`. Socket buffer overrun. (SP §10.6.5)
9. DNS: time a lookup with `getent hosts <name>`; read `/etc/resolv.conf`. Timeouts add seconds. (SP §10.3.5, §10.5.8)
10. Path latency: `ping -c 5 <host>`; `traceroute -n <host>`. Compare with a baseline. (SP §10.7.1, §10.7.2)
11. MTU: `ip link` MTU per interface; `ping -M do -s 1472 -c 3 <host>`. Large packets lost but small ones pass → MTU black hole. (SP §10.3.4)
12. Optional root: short `tcpdump -c 1000 -ni <if>`, `tcpretrans`, `tcplife` (BCC). Packet capture is costly. (SP §10.5.6)
Measure latency under load, not only on an idle system. (SP §10.5.4)

Key metrics: throughput vs limit, drops/errors, retransmit rate, resets, listen overflows, RTT, DNS latency.
Common root causes:
- DNS slow or timing out (bad resolver, search domains, no cache). (SP §10.3.5)
- Packet loss → TCP retransmits → latency outliers of 200 ms to seconds. (SP §2.8.6, §10.5.4)
- Server accept backlog full because the app is slow (CPU, GC, locks). The network is a symptom. (SP §10.3.7)
- Interface or cloud bandwidth cap reached. (SP §10.5.2)
- Link negotiated at lower speed or half duplex. (SP §10.3.8, §2.5.17)
- MTU mismatch with ICMP blocked by a firewall. (SP §10.3.4)
- Ephemeral port exhaustion from high connection churn. (SP §10.5.7)
- Container networking overhead (iptables rules, kube-proxy) at scale. (SP §11.3.2)
- Peer resets: peer crashed, peer limit reached, or idle timeout on a load balancer. `[ops]`
Before you blame the network, show data. Check the app and host first. (SP §2.5.3)
