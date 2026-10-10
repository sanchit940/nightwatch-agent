# USE Checklist: Linux (read-only)

Source: SP = Gregg, *Systems Performance* 2e. Based on SP Appendix A, §2.5.9, §6.5.2, §7.4.2, §9.5.2, §10.5.2, §11.3.4.
`[ops]` = general Linux practice, not from SP. Thresholds are heuristics. Compare with a baseline when you can.
Order: check **E**, then **S**, then **U** for each row group. Give each command a count (for example `1 5`).

## CPU — SP App. A, §6.5.2, §6.6

| | Command / file | Bad sign |
|-|----------------|----------|
| E | `dmesg -T \| grep -iE 'mce\|machine check\|thrott'` | Any machine-check or "clock throttled" line. |
| E | `cat /sys/devices/system/cpu/online` vs `nproc --all` `[ops]` | CPUs offline (kernel may offline a CPU after ECC errors). |
| S | `vmstat 1 5`: column `r` | `r` > CPU count, sustained. (`r` counts running + waiting.) |
| S | `cat /proc/pressure/cpu` | `some avg10` > 0 and rising. Any sustained value is saturation. |
| S | `sar -q 1 5`: `runq-sz` | `runq-sz` > CPU count. |
| S | `cat /proc/PID/schedstat` (2nd field = run-queue wait ns) | Field grows fast between two reads. |
| U | `mpstat -P ALL 1 5` | One CPU near 100% while others idle: single-thread limit. All CPUs > ~90%: CPU bound. |
| U | `vmstat 1 5`: `us`+`sy` | > ~60% hides bursts; 100% is a bottleneck. High `sy`: kernel work (syscalls, interrupts). |
| U | `pidstat 1 5`, `top -b -n 1` | Unexpected process using CPU. |
| U | `mpstat`/`vmstat`: `%steal` / `st` | > a few % sustained: hypervisor or other tenants take CPU. (SP §11.2.4.2) |

Note: load average includes tasks in D state (disk wait). Do not use it alone as a CPU metric. (SP App. A notes)

## Memory capacity — SP App. A, §7.4.1, §7.4.2

| | Command / file | Bad sign |
|-|----------------|----------|
| E | `dmesg -T \| grep -iE 'out of memory\|oom\|killed process'` | Any OOM kill. Record victim, time, and cgroup. |
| E | `dmesg -T \| grep -iE 'edac\|ecc\|memory error'` | Correctable ECC errors: hardware risk. |
| S | `vmstat 1 5`: `si`, `so` | Non-zero swap-in/out, sustained. |
| S | `sar -B 1 5`: `pgscank/s`, `pgscand/s` | Page scanning for more than ~10 s. (SP §7.4.1) |
| S | `cat /proc/pressure/memory` | `some` or `full` avg10 > 0 and above avg300 (pressure rising). |
| U | `free -m`: `available`; `grep -E 'MemAvailable\|MemTotal' /proc/meminfo` | `available` < ~10% of total `[ops]`. Page cache counts as available. Low `free` alone is normal. (SP §7.2.6) |
| U | `top -b -n 1 -o %MEM`, `ps aux --sort=-rss \| head` | One process RSS grows over time: leak or growth. (SP §7.4.6) |
| U | `slabtop -o -s c \| head` | Kernel slab very large (for example dentry, inode). |
| U | `swapon --show`; `free -m` (Swap row) | Swap nearly full. |

## Network interfaces — SP App. A, §10.5.2, §10.6

| | Command / file | Bad sign |
|-|----------------|----------|
| E | `ip -s link` (errors); `cat /proc/net/dev` | `errors`, `dropped`, `overrun` counters increase between reads. |
| E | `sar -n EDEV 1 5` | Non-zero `rxerr/s`, `txerr/s`, `rxdrop/s`, `rxfifo/s`. |
| S | `nstat -s \| grep -E 'TcpRetransSegs\|TcpOutSegs'` | Retrans / OutSegs > ~1% inside a data center `[ops]`. Internet clients have higher normal rates. |
| S | `netstat -s \| grep -iE 'pruned\|collapsed\|overflow\|SYNs to LISTEN'` | Receive-queue pruning: socket buffer overrun. Listen overflows: accept backlog full. |
| S | `ss -ltn` (Recv-Q on LISTEN sockets) `[ops]` | Recv-Q near Send-Q (backlog limit) on a listener. |
| U | `sar -n DEV 1 5`: `rxkB/s`, `txkB/s` | Throughput near link speed in either direction. |
| U | `cat /sys/class/net/<if>/speed` `[ops]`; `ethtool <if>` | Link negotiated below expected speed, or half duplex. (SP §2.5.17) |

## Storage devices (disk I/O) — SP App. A, §9.5.2, §9.6.1

| | Command / file | Bad sign |
|-|----------------|----------|
| E | `dmesg -T \| grep -iE 'i/o error\|blk_update_request\|reset\|timeout'` | Any I/O error or device reset. |
| E | `cat /sys/block/<dev>/device/ioerr_cnt` | Count > 0 or rising. |
| E | `smartctl -H /dev/<dev>` (optional, needs root) | SMART health not PASSED. |
| S | `iostat -sxz 1 5`: `aqu-sz`, `await` | `aqu-sz` > 1 sustained. `await` far above the device baseline. |
| S | `cat /proc/pressure/io` | `full avg10` > 0 and rising. |
| U | `iostat -sxz 1 5`: `%util` | > 60% adds queueing. 100% for several seconds is very likely an issue. (SP §9.5.3) |
| U | `pidstat -d 1 5` | Unexpected process doing disk I/O (backup, log, batch job). |

Note: `%util` is weak for virtual, RAID, network, or cloud volumes. Use `await`, IOPS, and throughput there. (SP §9.3.9)
Note: high `%iowait` only means CPUs were idle while I/O was pending. It is not a disk metric. `[ops]`

## Storage capacity and file systems — SP App. A, §2.5.17

| | Command / file | Bad sign |
|-|----------------|----------|
| E | App logs, `dmesg`: `ENOSPC`, "No space left on device" | Writes fail. |
| E | `dmesg -T \| grep -iE 'ext4-fs error\|xfs.*error\|remount.*read-only'` `[ops]` | FS error; FS remounted read-only. |
| E | `grep -w ro /proc/mounts` `[ops]` | A data FS that should be writable is mounted `ro`. |
| U | `df -h` | > ~90% used. Nearly full FS can also slow down. (SP App. A, §2.5.17) |
| U | `df -i` `[ops]` | IUse% near 100%: inode exhaustion. ENOSPC even though `df -h` shows free space. |
| U | `lsof +L1` (optional, needs root) `[ops]` | Deleted files still open: space not freed. |

## File descriptors and task capacity — SP App. A (software resources)

| | Command / file | Bad sign |
|-|----------------|----------|
| E | App logs: `EMFILE`, "Too many open files" | Any occurrence. |
| E | App logs: "can't fork", `EAGAIN` from fork/pthread_create | Task limit reached. |
| U | `cat /proc/sys/fs/file-nr` (allocated, free, max) | Allocated near max. |
| U | `ls /proc/<PID>/fd \| wc -l` vs `grep 'open files' /proc/<PID>/limits` | Count > ~80% of soft limit `[ops]`. |
| U | `ps -eLf --no-headers \| wc -l` vs `cat /proc/sys/kernel/threads-max` and `pid_max` | Thread count near a limit. |
| U | `grep -E 'processes' /proc/<PID>/limits` `[ops]` | Per-user process limit near the thread count of that user. |

## Containers / cgroups — SP §11.3.3, §11.3.4

Find the cgroup: `cat /proc/<PID>/cgroup`. v2 path: `/sys/fs/cgroup/<path>/`. v1 paths: `/sys/fs/cgroup/<controller>/<path>/`.
Read each counter twice, about 10 s apart. Use the change, not the total. (SP §11.3.4.3)

| Resource | | v2 file (v1 file) | Bad sign |
|----------|-|-------------------|----------|
| CPU limit | U | `cpu.max` (`cpu.cfs_quota_us` / `cpu.cfs_period_us`) `[ops v2]` | Usage near quota/period CPUs. |
| CPU throttle | S | `cpu.stat`: `nr_throttled`, `throttled_usec` (v1 `throttled_time` ns) | Values increase between reads: CPU bandwidth throttling. |
| CPU usage | U | `cpu.stat`: `usage_usec` (`cpuacct.usage`) | Usage / elapsed time near the CPU limit. |
| CPU pressure | S | `cpu.pressure` `[ops v2]` | `some avg10` > 0 and rising. |
| Memory | U | `memory.current` vs `memory.max` (`memory.usage_in_bytes` vs `memory.limit_in_bytes`) | Usage > ~90% of limit `[ops]`. Page cache counts here too. (SP §11.3.3) |
| Memory | E | `memory.events`: `oom_kill`, `max` (`memory.failcnt`, `memory.oom_control`) `[ops v2]` | `oom_kill` > 0 or rising; `max` rising. |
| Memory | S | `memory.pressure` (SP §7.5.2) | `full avg10` > 0. |
| Disk I/O | S | `io.stat`, `io.max`, `io.pressure` (`blkio.throttle.io_serviced`, `io_service_bytes`) | I/O rate equals the throttle rate: throttle-limited. (SP §11.3.4.4) |
| Tasks | U/E | `pids.current` vs `pids.max` `[ops]` | Current near max: fork failures follow. |
| Per-container view | U | `docker stats --no-stream`, `kubectl top pods`, `systemd-cgtop -n 1` | Container at its CPU or memory limit. |
| Pod state | E | `kubectl describe pod <p>`; `kubectl get events` `[ops]` | `OOMKilled`, exit code 137, restarts, `Evicted`. |

Warning: `top`, `free`, `vmstat`, `mpstat`, `iostat`, `uptime` inside a container usually show **host** values. (SP §11.3.4.1, Table 11.6)
