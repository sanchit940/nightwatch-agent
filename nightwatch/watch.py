"""Watch mode: cheap deterministic health checks on a schedule. No model calls while everything is healthy.

When a check fails for N consecutive rounds, NightWatch opens ONE incident per problem (deduplicated by key)
and starts an investigation. Repeated failures of the same problem do not create new incidents or alerts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

import yaml

from .config import ROOT
from .connectors.base import TOOLS


@dataclass
class Finding:
    key: str          # dedup key, for example "http:web" or "container:nw-api:unhealthy"
    symptom: str      # human-style description handed to the agent


def _tool(name: str, **args):
    t = TOOLS[name]
    return t.fn(t.input_model.model_validate(args))


def check_http(name: str, url: str, source: str = "agent", max_ms: float = 1500) -> list[Finding]:
    out = _tool("net_http_check", source=source, url=url, timeout_s=5)
    d = out.data or {}
    if not out.ok:
        return [Finding(f"http:{name}", f"Health check of {name} failed: GET {url} from {source} -> "
                                         f"{d.get('status') or d.get('error') or out.error_code}.")]
    if d.get("ms", 0) > max_ms:
        return [Finding(f"http-latency:{name}", f"{name} is slow: GET {url} took {d['ms']} ms (threshold {max_ms} ms).")]
    return []


def check_containers(prefix: str = "nw-") -> list[Finding]:
    out = _tool("docker_list_containers", include_stopped=True)
    if not out.ok:
        return [Finding("docker:daemon", f"Docker API is not reachable: {out.text[:200]}")]
    found = []
    for line in out.text.splitlines():
        cols = dict(c.split("=", 1) for c in line.split("\t")[1:] if "=" in c)
        name = line.split("\t", 1)[0]
        if not name.startswith(prefix):
            continue
        state, status = cols.get("state", ""), cols.get("status", "")
        if state in ("exited", "dead") :
            found.append(Finding(f"container:{name}:down", f"Container {name} is {state} ({status})."))
        elif state == "restarting":
            found.append(Finding(f"container:{name}:restarting", f"Container {name} is in a restart loop ({status})."))
        elif "unhealthy" in status:
            found.append(Finding(f"container:{name}:unhealthy", f"Container {name} reports unhealthy ({status})."))
    return found


def check_alerts() -> list[Finding]:
    out = _tool("prom_alerts")
    if not out.ok:
        return []  # monitoring outage is reported by its own check, not as an incident
    return [Finding(f"alert:{line.split(chr(9))[0]}", f"Prometheus alert firing: {line[:300]}")
            for line in out.text.splitlines() if "state=firing" in line]


def check_k8s(namespace: str) -> list[Finding]:
    out = _tool("k8s_get", kind="pods", namespace=namespace)
    if not out.ok:
        return []
    found = []
    for line in out.text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 3:
            continue
        name, ready, status = cols[0], cols[1], cols[2]
        a, _, b = ready.partition("/")
        if status not in ("Running", "Completed") or a != b:
            app = name.rsplit("-", 2)[0]
            found.append(Finding(f"k8s:{namespace}:{app}", f"Pod {namespace}/{name} is {status} with {ready} containers ready."))
    return found


def load_checks() -> list[Callable[[], list[Finding]]]:
    cfg = yaml.safe_load((ROOT / "watch.yaml").read_text())
    checks: list[Callable[[], list[Finding]]] = []
    for h in cfg.get("http", []):
        checks.append(lambda h=h: check_http(h["name"], h["url"], h.get("source", "agent"), h.get("max_ms", 1500)))
    if cfg.get("docker", {}).get("enabled"):
        checks.append(lambda: check_containers(cfg["docker"].get("prefix", "nw-")))
    if cfg.get("prometheus_alerts"):
        checks.append(check_alerts)
    for ns in cfg.get("kubernetes_namespaces", []):
        checks.append(lambda ns=ns: check_k8s(ns))
    return checks


def group(findings: list[Finding]) -> list[Finding]:
    """Many symptoms of one outage arrive together. Merge one round of findings into one incident."""
    if not findings:
        return []
    keys = sorted({f.key for f in findings})
    return [Finding("|".join(keys), "Monitoring detected these symptoms at the same time:\n- " + "\n- ".join(f.symptom for f in findings))]


def scan(checks) -> list[Finding]:
    found: list[Finding] = []
    for c in checks:
        try:
            found.extend(c())
        except Exception as e:  # noqa: BLE001
            found.append(Finding(f"watch-error:{getattr(c, '__name__', 'check')}", f"A watch check failed to run: {e}"))
    return found


def run_watch(handle_incident: Callable[[Finding], None], interval_s: float = 20, confirm_rounds: int = 2,
              on_tick: Callable[[list[Finding]], None] | None = None, once: bool = False) -> None:
    checks = load_checks()
    streak: dict[str, int] = {}
    active: set[str] = set()
    while True:
        findings = scan(checks)
        if on_tick:
            on_tick(findings)
        keys = {f.key for f in findings}
        for k in list(streak):
            if k not in keys:
                streak.pop(k)
                active.discard(k)   # recovered: a new failure later opens a new incident
        for f in findings:
            streak[f.key] = streak.get(f.key, 0) + 1
        confirmed = [f for f in findings if streak[f.key] >= confirm_rounds and f.key not in active]
        if confirmed:
            for f in confirmed:
                active.add(f.key)
            handle_incident(group(confirmed)[0])
        if once:
            return
        time.sleep(interval_s)
