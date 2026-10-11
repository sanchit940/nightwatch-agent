"""Linux connector: approved read-only diagnostics, executed inside a target Linux container or node.

Every check is a constant script. The model selects a check by name; it never supplies command text.
The checks follow the USE method (utilization, saturation, errors) from Gregg, Systems Performance 2e,
Appendix A, and read cgroup files because host-wide tools show host values inside a container (SP 11.3.4).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field

from ..runner import run
from .base import Args, RiskLevel, ToolOutput, tool

TARGET = Annotated[str, Field(pattern=r"^(host:)?[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
                               description="Linux target: a container name (for example nw-api), a kind node container (nightwatch-control-plane), "
                                           "or a server reached over SSH as host:<name> from the inventory (for example host:nw-vm)")]

_HAVE = "have() { command -v \"$1\" >/dev/null 2>&1; }; "

CHECKS: dict[str, tuple[str, str]] = {
    "load": ("Load average, CPU count, uptime. Load includes D-state tasks; do not use it alone.",
             "cat /proc/loadavg; echo cpus=$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo); cat /proc/uptime"),
    "pressure": ("Pressure stall information (PSI) for cpu, memory, io. Rising avg10 means saturation.",
                 "for r in cpu memory io; do echo \"== $r\"; cat /proc/pressure/$r 2>/dev/null || echo unavailable; done"),
    "vmstat": ("vmstat 1 5 (run queue r, swap si/so, us/sy/id/wa/st). Falls back to /proc/stat deltas.",
               _HAVE + "if have vmstat; then vmstat 1 5; else head -1 /proc/stat; sleep 2; head -1 /proc/stat; fi"),
    "memory": ("/proc/meminfo summary (host view inside containers; use 'cgroup' for container limits).",
               "grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|Dirty|Slab|Committed_AS):' /proc/meminfo"),
    "cgroup": ("Container cgroup v2/v1 limits and counters: cpu.max, cpu.stat (throttling, read twice 5 s apart), memory.current/max, memory.events (oom_kill), pids, PSI.",
               "cg=/sys/fs/cgroup; if [ -f $cg/cpu.max ]; then "
               "for f in cpu.max memory.current memory.max memory.peak memory.swap.current pids.current pids.max; do "
               "[ -f $cg/$f ] && echo \"$f: $(cat $cg/$f)\"; done; "
               "echo '== memory.events'; cat $cg/memory.events 2>/dev/null; "
               "echo '== memory.stat (top)'; grep -E '^(anon|file|shmem|sock|kernel) ' $cg/memory.stat 2>/dev/null; "
               "echo '== cpu.stat t0'; cat $cg/cpu.stat; sleep 5; echo '== cpu.stat t0+5s'; cat $cg/cpu.stat; "
               "for p in cpu memory io; do echo \"== $p.pressure\"; cat $cg/$p.pressure 2>/dev/null; done; "
               "else echo 'cgroup v1'; for f in cpu/cpu.cfs_quota_us cpu/cpu.cfs_period_us cpu/cpu.stat memory/memory.usage_in_bytes "
               "memory/memory.limit_in_bytes memory/memory.failcnt memory/memory.oom_control pids/pids.current pids/pids.max; do "
               "[ -f $cg/$f ] && echo \"$f: $(cat $cg/$f | tr '\\n' ' ')\"; done; fi"),
    "disk": ("File system capacity and inodes (df -h, df -i). Capacity, not performance.",
             "df -h 2>&1 | grep -vE '^(overlay|shm|tmpfs) .* /(proc|sys|dev)' ; echo '== inodes'; df -i 2>&1"),
    "mounts": ("Mounted file systems with options; shows read-only (ro) mounts.",
               "grep -vE ' (proc|sysfs|cgroup2?|devpts|mqueue|securityfs|debugfs|tracefs|fusectl|configfs|bpf) ' /proc/mounts"),
    "io": ("Block device I/O counters (/proc/diskstats, two reads 3 s apart) and io pressure.",
           "echo '== t0'; grep -vE ' (loop|ram)[0-9]' /proc/diskstats; sleep 3; echo '== t0+3s'; grep -vE ' (loop|ram)[0-9]' /proc/diskstats; cat /proc/pressure/io 2>/dev/null"),
    "processes": ("Processes with parent PID, state, AGE (seconds since start; compare with the container uptime to spot processes that appeared recently), CPU time, RSS and command, sorted by RSS. D state = uninterruptible (I/O) wait.",
                  _HAVE + "if have ps && ps -eo pid >/dev/null 2>&1; then ps -eo pid,ppid,stat,pcpu,rss,etimes,comm,args --sort=-rss | head -40; else "
                  "hz=$(getconf CLK_TCK 2>/dev/null || echo 100); up=$(cut -d. -f1 /proc/uptime); "
                  "echo 'PID PPID STATE AGE_S RSS_KB UTIME_TICKS CMD'; for d in /proc/[0-9]*; do p=${d#/proc/}; "
                  "set -- $(cut -d' ' -f3,4,14,22 $d/stat 2>/dev/null); [ -n \"$4\" ] || continue; "
                  "r=$(grep VmRSS $d/status 2>/dev/null | tr -s ' ' | cut -d' ' -f2); "
                  "c=$(tr '\\0' ' ' < $d/cmdline 2>/dev/null | cut -c1-120); case \"$c\" in *'have() {'*) continue;; esac; echo \"$p $2 $1 $((up - $4 / hz)) ${r:-0} $3 $c\"; done | sort -k5 -n -r | head -40; fi"),
    "cpu_top": ("Per-process CPU usage sampled over 3 s (from /proc/<pid>/stat utime+stime deltas).",
                "snap() { for d in /proc/[0-9]*; do set -- $(cut -d' ' -f1,14,15 $d/stat 2>/dev/null); [ -n \"$1\" ] && echo \"$1 $(($2+$3))\"; done; }; "
                "snap > /tmp/.nw_a 2>/dev/null || snap > /dev/shm/.nw_a; sleep 3; snap > /tmp/.nw_b 2>/dev/null || snap > /dev/shm/.nw_b; "
                "a=/tmp/.nw_a; [ -f $a ] || a=/dev/shm/.nw_a; b=${a%_a}_b; hz=$(getconf CLK_TCK 2>/dev/null || echo 100); "
                "echo \"PID CPU% CMD (hz=$hz)\"; while read pid t; do t0=$(grep \"^$pid \" $a | cut -d' ' -f2); "
                "[ -n \"$t0\" ] && echo \"$pid $(( (t - t0) * 100 / (3 * hz) )) $(tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-100)\"; "
                "done < $b | sort -k2 -n -r | head -15; rm -f $a $b"),
    "open_files": ("System file handles (file-nr) and per-process open fd count vs soft limit.",
                   "echo \"file-nr: $(cat /proc/sys/fs/file-nr)\"; for d in /proc/[0-9]*; do n=$(ls $d/fd 2>/dev/null | wc -l); "
                   "l=$(grep 'open files' $d/limits 2>/dev/null | tr -s ' ' | cut -d' ' -f4); "
                   "echo \"${d#/proc/} fds=$n soft_limit=$l $(cut -c1-60 $d/comm 2>/dev/null)\"; done | sort -t= -k2 -n -r | head -15"),
    "net_interfaces": ("Interfaces with addresses and packet/error/drop counters.",
                       _HAVE + "if have ip; then ip -s addr 2>/dev/null || ip addr; fi; echo '== /proc/net/dev'; cat /proc/net/dev"),
    "routes": ("Routing table and default gateway.",
               _HAVE + "if have ip; then ip route; else echo 'Iface Dest(hex) Gateway(hex) Flags'; awk 'NR>1{print $1,$2,$3,$4}' /proc/net/route; fi"),
    "listening": ("Listening TCP ports (from ss/netstat or decoded /proc/net/tcp).",
                  _HAVE + "if have ss; then ss -ltnp; elif have netstat; then netstat -ltn; else "
                  "echo 'local_port(dec) state=LISTEN'; for f in /proc/net/tcp /proc/net/tcp6; do [ -f $f ] && "
                  "while read sl local rem st rest; do [ \"$st\" = 0A ] && echo \"$f port=$(printf %d 0x${local##*:})\"; done < $f; done; fi"),
    "tcp_stats": ("TCP counters: retransmits, resets, failed connects, listen overflows (/proc/net/snmp and netstat), and connection states.",
                  "grep -A1 '^Tcp:' /proc/net/snmp; grep -A1 '^TcpExt:' /proc/net/netstat | tr ' ' '\\n' | paste - - 2>/dev/null | "
                  "grep -E 'ListenOverflows|ListenDrops|TCPTimeouts|TCPAbortOnTimeout|TCPSynRetrans' || true; "
                  "echo '== socket states (hex: 01 ESTABLISHED 02 SYN_SENT 06 TIME_WAIT 08 CLOSE_WAIT 0A LISTEN)'; "
                  "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | awk 'NR>1 && $4 ~ /^[0-9A-F][0-9A-F]$/ {c[$4]++} END {for (s in c) print s, c[s]}'"),
    "dns_config": ("Resolver configuration (/etc/resolv.conf, /etc/hosts, nsswitch hosts line).",
                   "echo '== resolv.conf'; cat /etc/resolv.conf; echo '== hosts'; cat /etc/hosts; grep '^hosts' /etc/nsswitch.conf 2>/dev/null"),
    "kernel_log": ("Last kernel messages (dmesg). Often not permitted inside unprivileged containers.",
                   "dmesg 2>&1 | tail -n 80"),
    "systemd": ("Failed systemd units and recent error-priority journal entries (nodes with systemd only).",
                _HAVE + "if have systemctl; then systemctl --failed --no-pager; journalctl -p err -n 40 --no-pager 2>&1 | tail -n 40; else echo 'systemd not available on this target'; fi"),
    "journal": ("systemd journal: warning-or-worse entries of the last 30 minutes, all units (servers with journald).",
                _HAVE + "if have journalctl; then journalctl -p warning --since -30min -n 80 --no-pager -o short-iso 2>&1 | tail -n 80; "
                "else echo 'journald not available on this target'; fi"),
    "time": ("Current UTC time on the target (compare with the agent clock for clock skew).",
             "date -u +%Y-%m-%dT%H:%M:%SZ; cat /proc/uptime"),
}

CheckName = Enum("CheckName", {k: k for k in CHECKS})


class LinuxCheckArgs(Args):
    target: TARGET
    check: CheckName = Field(description="; ".join(f"{k}: {v[0]}" for k, v in CHECKS.items()))


def _ssh_hosts() -> dict:
    import yaml
    from ..config import ROOT
    inv = yaml.safe_load((ROOT / "inventory.yaml").read_text()) or {}
    hosts = {h["name"]: h for h in inv.get("ssh_hosts", []) or []}
    local = ROOT / "inventory.local.yaml"
    if local.exists():
        hosts.update({h["name"]: h for h in (yaml.safe_load(local.read_text()) or {}).get("ssh_hosts", []) or []})
    return hosts


def _argv(target: str, script: str, *args: str) -> list[str] | str:
    """Fixed argv for a container (docker exec) or a server (ssh, key only, known host keys only)."""
    if not target.startswith("host:"):
        return ["docker", "exec", target, "sh", "-c", script, "_", *args]
    import shlex
    from ..config import ROOT
    h = _ssh_hosts().get(target[5:])
    if not h:
        return f"unknown host {target[5:]}: add it to ssh_hosts in inventory.yaml"
    path = lambda p: str((ROOT / p).resolve()) if p and not str(p).startswith("/") else str(p)
    remote = " ".join(shlex.quote(x) for x in ["sh", "-c", script, "_", *args])
    return ["ssh", "-i", path(h["key_file"]), "-p", str(h.get("port", 22)),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={path(h['known_hosts'])}",
            "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "LogLevel=ERROR",
            f"{h['user']}@{h['address']}", remote]


def _exec(target: str, script: str, timeout: float = 20, args: tuple[str, ...] = ()) -> ToolOutput:
    argv = _argv(target, script, *args)
    if isinstance(argv, str):
        return ToolOutput(False, argv, "NOT_FOUND")
    res = run(argv, timeout=timeout, max_bytes=14000)
    if target.startswith("host:") and res.exit_code == 255:
        code = "HOST_KEY_UNKNOWN" if "Host key verification failed" in res.stderr else "SSH_FAILED"
        return ToolOutput(False, f"ssh failed: {res.stderr.strip()[:300]}", code)
    # Do not echo the script back to the model: it is constant and wastes tokens.
    text = f"exit={res.exit_code} duration_ms={res.duration_ms}\n{res.stdout}"
    if res.stderr.strip():
        text += f"\n[stderr]\n{res.stderr}"
    if res.exit_code == 1 and "No such container" in res.stderr:
        return ToolOutput(False, text, "NOT_FOUND")
    if res.exit_code in (126, 127) or "Error response from daemon" in res.stderr:  # not running, restarting, paused
        return ToolOutput(False, text, "TARGET_UNAVAILABLE")
    # Diagnostic scripts can exit non-zero when one optional file is missing; keep the output.
    return ToolOutput(res.error_code is None, text, res.error_code)


@tool("linux_check", "Run ONE approved read-only Linux diagnostic on a target. Choose the check that best tests your current hypothesis.",
      LinuxCheckArgs, RiskLevel.READ, "linux", timeout_s=25, targets=lambda a: [f"linux:{a.target}"])
def linux_check(a: LinuxCheckArgs) -> ToolOutput:
    name = a.check.value
    return _exec(a.target, CHECKS[name][1])


class SnapshotArgs(Args):
    target: TARGET


@tool("linux_use_snapshot",
      "USE-method snapshot of a Linux target in one call: load, PSI pressure, memory, cgroup limits/throttling/OOM counters, disk capacity+inodes, top processes. Use it first for any host or container resource question.",
      SnapshotArgs, RiskLevel.READ, "linux", timeout_s=40, targets=lambda a: [f"linux:{a.target}"])
def linux_use_snapshot(a: SnapshotArgs) -> ToolOutput:
    parts = []
    ok = True
    for name in ("load", "pressure", "memory", "cgroup", "disk", "processes"):
        out = _exec(a.target, CHECKS[name][1], timeout=30)
        if out.error_code in ("NOT_FOUND", "TARGET_UNAVAILABLE"):
            return out
        ok = ok and out.ok
        parts.append(f"### {name}\n{out.text}")
    return ToolOutput(ok, "\n".join(parts)[:16000])


class ServiceArgs(Args):
    target: TARGET
    unit: str = Field(pattern=r"^[A-Za-z0-9@._-]{1,100}\.(service|timer|socket|mount)$", description="systemd unit, for example shop-worker.service")
    lines: int = Field(50, ge=5, le=200)


@tool("linux_service_status", "Status of one systemd unit (active/failed, restarts, last exit code) and its last journal lines. For servers with systemd.",
      ServiceArgs, RiskLevel.READ, "linux", timeout_s=25, targets=lambda a: [f"linux:{a.target}"])
def linux_service_status(a: ServiceArgs) -> ToolOutput:
    script = (_HAVE + 'if ! have systemctl; then echo "systemd not available on this target"; exit 0; fi; '
              'systemctl status "$1" --no-pager -n 0 2>&1 | head -20; '
              'echo "== properties"; systemctl show "$1" -p ActiveState,SubState,Result,NRestarts,ExecMainStatus,ExecMainExitTimestamp --no-pager; '
              'echo "== recent errors and warnings (newest last)"; journalctl -u "$1" -p warning -n 15 --no-pager -o short-iso 2>&1 | tail -n 15; '
              'echo "== journal (all priorities, newest last)"; journalctl -u "$1" -n "$2" --no-pager -o short-iso 2>&1 | tail -n "$2"')
    return _exec(a.target, script, args=(a.unit, str(a.lines)))
