"""Monitoring connector: Prometheus (instant and range queries, active alerts, scrape targets) and runbooks."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import Field

from .base import Args, RiskLevel, ToolOutput, tool


def _prom(path: str, params: dict | None = None, timeout: float = 10) -> dict:
    base = os.environ.get("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
    url = f"{base}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"status": "error", "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"status": "error", "errorType": "unavailable", "error": str(e)}


class QueryArgs(Args):
    promql: str = Field(min_length=1, max_length=500, description="PromQL expression")


@tool("prom_query", "Prometheus instant query (current value). Example: sum by (status) (rate(http_requests_total[1m])).",
      QueryArgs, RiskLevel.READ, "monitoring", targets=lambda a: ["prometheus"])
def prom_query(a: QueryArgs) -> ToolOutput:
    d = _prom("/api/v1/query", {"query": a.promql})
    if d.get("status") != "success":
        return ToolOutput(False, json.dumps(d), "UNAVAILABLE" if d.get("errorType") == "unavailable" else "QUERY_ERROR")
    rows = [f"{json.dumps(s.get('metric'))} = {s.get('value', [None, None])[1]}" for s in d["data"]["result"][:50]]
    return ToolOutput(True, "\n".join(rows) or "(empty result: metric absent or no matching series)")


class RangeArgs(Args):
    promql: str = Field(min_length=1, max_length=500)
    minutes: int = Field(30, ge=1, le=1440, description="Look-back window")
    step_s: int = Field(30, ge=5, le=3600)


@tool("prom_query_range",
      "Prometheus range query summarized per series: first, min, max, last value and the time the value changed most. Use it to find when a problem started and to compare with the healthy period.",
      RangeArgs, RiskLevel.READ, "monitoring", targets=lambda a: ["prometheus"])
def prom_query_range(a: RangeArgs) -> ToolOutput:
    end = time.time()
    d = _prom("/api/v1/query_range", {"query": a.promql, "start": end - a.minutes * 60, "end": end, "step": a.step_s})
    if d.get("status") != "success":
        return ToolOutput(False, json.dumps(d), "UNAVAILABLE" if d.get("errorType") == "unavailable" else "QUERY_ERROR")
    out = []
    for s in d["data"]["result"][:20]:
        pts = [(float(t), float(v)) for t, v in s["values"] if v not in ("NaN", "+Inf", "-Inf")]
        if not pts:
            continue
        jumps = [(abs(pts[i][1] - pts[i - 1][1]), pts[i][0]) for i in range(1, len(pts))]
        big = max(jumps)[1] if jumps else pts[0][0]
        ts = lambda t: time.strftime("%H:%M:%S", time.gmtime(t)) + "Z"
        sample = " ".join(f"{ts(t)}={v:.4g}" for t, v in pts[:: max(1, len(pts) // 8)])
        out.append(f"{json.dumps(s['metric'])}\n  first={pts[0][1]:.4g} min={min(v for _, v in pts):.4g} max={max(v for _, v in pts):.4g} "
                   f"last={pts[-1][1]:.4g} biggest_change_at={ts(big)}\n  samples: {sample}")
    return ToolOutput(True, "\n".join(out) or "(no data in window)")


class NoArgs(Args):
    pass


@tool("prom_alerts", "Active Prometheus alerts with state (pending/firing), labels and start time. An alert can be a symptom, not a cause.",
      NoArgs, RiskLevel.READ, "monitoring", targets=lambda a: ["prometheus"])
def prom_alerts(a: NoArgs) -> ToolOutput:
    d = _prom("/api/v1/alerts")
    if d.get("status") != "success":
        return ToolOutput(False, json.dumps(d), "UNAVAILABLE")
    rows = [f"{al['labels'].get('alertname')}\tstate={al['state']}\tsince={al.get('activeAt')}\tlabels={json.dumps(al['labels'])}"
            f"\tsummary={al.get('annotations', {}).get('summary', '')}" for al in d["data"]["alerts"]]
    return ToolOutput(True, "\n".join(rows) or "(no active alerts)")


@tool("prom_targets", "Prometheus scrape target health (up/down, last error). A down target means missing data, not always a down service.",
      NoArgs, RiskLevel.READ, "monitoring", targets=lambda a: ["prometheus"])
def prom_targets(a: NoArgs) -> ToolOutput:
    d = _prom("/api/v1/targets", {"state": "active"})
    if d.get("status") != "success":
        return ToolOutput(False, json.dumps(d), "UNAVAILABLE")
    rows = [f"{t['labels'].get('job')}\t{t.get('scrapeUrl')}\thealth={t.get('health')}\tlast_error={t.get('lastError') or '-'}"
            for t in d["data"]["activeTargets"]]
    return ToolOutput(True, "\n".join(rows) or "(no targets)")


# ---------- runbooks (knowledge base) ----------

KNOWLEDGE = Path(__file__).resolve().parent.parent.parent / "knowledge"


class RunbookArgs(Args):
    topic: str = Field(pattern=r"^[a-z0-9-]{1,60}$", description="Runbook file name without .md; call with topic='index' to list them")
    section: str | None = Field(None, max_length=60, description="Optional heading text to return only that section")


@tool("get_runbook", "Read an approved runbook from the knowledge base (methods from Systems Performance 2e and ops practice).",
      RunbookArgs, RiskLevel.READ, "knowledge")
def get_runbook(a: RunbookArgs) -> ToolOutput:
    files = sorted(p.stem for p in KNOWLEDGE.glob("*.md"))
    if a.topic == "index":
        heads = []
        for f in files:
            hs = [l.strip("# ").strip() for l in (KNOWLEDGE / f"{f}.md").read_text().splitlines() if l.startswith("## ")]
            heads.append(f"{f}: " + " | ".join(hs))
        return ToolOutput(True, "\n".join(heads))
    if a.topic not in files:
        return ToolOutput(False, f"unknown runbook. available: {files}", "NOT_FOUND")
    text = (KNOWLEDGE / f"{a.topic}.md").read_text()
    if a.section:
        blocks = text.split("\n## ")
        hit = [b for b in blocks if a.section.lower() in b.splitlines()[0].lower()]
        text = ("## " + "\n## ".join(hit)) if hit else f"section not found; headings: {[b.splitlines()[0] for b in blocks[1:]]}"
    return ToolOutput(True, text[:12000])
