"""neatlogs connector: investigate AI-agent incidents from neatlogs traces, spans, alert history and detections.

Read-only. Uses the neatlogs Public API (https://app.neatlogs.com/api/v1/public).
Credentials: NEATLOGS_READ_TOKEN (service-account token: Bearer) or NEATLOGS_PUBLIC_API_KEY (x-api-key),
and NEATLOGS_PROJECT_ID (project UUID). The SDK ingest key cannot read.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated

from pydantic import Field

from .base import Args, RiskLevel, ToolOutput, tool

TRACE_ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{6,128}$")]


def _call(method: str, path: str, query: dict | None = None, body: dict | None = None, timeout: float = 20) -> tuple[bool, dict | str, str | None]:
    base = os.environ.get("NEATLOGS_API_URL", "https://app.neatlogs.com").rstrip("/")
    headers = {"Accept": "application/json", "User-Agent": "nightwatch/0.1"}
    if tok := os.environ.get("NEATLOGS_READ_TOKEN"):
        headers["Authorization"] = f"Bearer {tok}"
    elif key := os.environ.get("NEATLOGS_PUBLIC_API_KEY"):
        headers["x-api-key"] = key
    else:
        return False, "neatlogs read credentials are not configured (NEATLOGS_READ_TOKEN or NEATLOGS_PUBLIC_API_KEY)", "NOT_CONFIGURED"
    if pid := os.environ.get("NEATLOGS_PROJECT_ID"):
        headers["x-project-id"] = pid
    q = {k: v for k, v in (query or {}).items() if v is not None}
    url = f"{base}/api/v1/public{path}" + (f"?{urllib.parse.urlencode(q)}" if q else "")
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=timeout) as r:
            return True, json.loads(r.read()).get("data", {}), None
    except urllib.error.HTTPError as e:
        try:
            p = json.loads(e.read())
            return False, f"HTTP {e.code} {p.get('code')}: {p.get('detail')}", f"HTTP_{e.code}"
        except Exception:  # noqa: BLE001
            return False, f"HTTP {e.code}", f"HTTP_{e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, str(e), "UNAVAILABLE"


def _iso(minutes_ago: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes_ago * 60))


def _pick(d: dict, keys: tuple) -> dict:
    return {k: d.get(k) for k in keys if d.get(k) not in (None, [], "")}


TRACE_KEYS = ("traceId", "traceName", "workflowName", "status", "startedAt", "durationMs", "llmCallsCount", "toolCallsCount",
              "spansCount", "totalTokens", "totalCostUsd", "sessionId", "tags")
SPAN_KEYS = ("spanId", "parentSpanId", "spanName", "spanType", "status", "startedAt", "durationMs", "model", "totalTokens", "costUsd")


class TracesArgs(Args):
    minutes: int = Field(60, ge=1, le=10080, description="Look-back window")
    status: str | None = Field(None, pattern=r"^[a-z_]{2,20}$", description="Filter by trace status, for example error")
    workflow: str | None = Field(None, max_length=100, description="Filter by workflow name")
    limit: int = Field(20, ge=1, le=100)


@tool("neatlogs_traces", "List recent AI-agent traces from neatlogs with status, duration, LLM/tool call counts, tokens and cost. Use it for incidents in AI agents (errors, slowness, cost spikes).",
      TracesArgs, RiskLevel.READ, "neatlogs", targets=lambda a: ["neatlogs:traces"])
def neatlogs_traces(a: TracesArgs) -> ToolOutput:
    ok, d, code = _call("GET", "/traces", {"from": _iso(a.minutes), "status": a.status, "workflowName": a.workflow, "limit": a.limit})
    if not ok:
        return ToolOutput(False, str(d), code)
    rows = [json.dumps(_pick(t, TRACE_KEYS)) for t in d.get("traces", [])]
    return ToolOutput(True, "\n".join(rows) or "(no traces in window)")


class SpansArgs(Args):
    trace_id: TRACE_ID
    status: str | None = Field(None, pattern=r"^[a-z_]{2,20}$")
    limit: int = Field(50, ge=1, le=200)


@tool("neatlogs_trace_spans", "List the spans of one neatlogs trace (LLM calls, tools, retrievers) with status, duration, model, tokens and cost, to find the failing or slow step.",
      SpansArgs, RiskLevel.READ, "neatlogs", targets=lambda a: [f"neatlogs:trace/{a.trace_id}"])
def neatlogs_trace_spans(a: SpansArgs) -> ToolOutput:
    ok, d, code = _call("GET", f"/traces/{a.trace_id}/spans", {"status": a.status, "limit": a.limit})
    if not ok:
        return ToolOutput(False, str(d), code)
    rows = [json.dumps(_pick(s, SPAN_KEYS)) for s in d.get("spans", [])]
    return ToolOutput(True, "\n".join(rows) or "(no spans)")


class SearchArgs(Args):
    query: str = Field(min_length=2, max_length=200, description="Keyword, for example an error message or a tool name")
    minutes: int = Field(1440, ge=1, le=10080)
    limit: int = Field(10, ge=1, le=50)


@tool("neatlogs_search", "Keyword search across neatlogs traces (error messages, tool names, prompts). Returns matching traces and spans.",
      SearchArgs, RiskLevel.READ, "neatlogs", targets=lambda a: ["neatlogs:search"])
def neatlogs_search(a: SearchArgs) -> ToolOutput:
    ok, d, code = _call("POST", "/search/traces", body={"q": a.query, "from": _iso(a.minutes), "limit": a.limit})
    if not ok:
        return ToolOutput(False, str(d), code)
    rows = []
    for r in d.get("results", []):
        t = _pick(r.get("trace") or {}, TRACE_KEYS)
        t["matchedSpans"] = [_pick(s, ("spanName", "spanType", "status")) for s in (r.get("matchedSpans") or [])[:3]]
        rows.append(json.dumps(t))
    return ToolOutput(True, "\n".join(rows) or "(no matches)")


class AlertsArgs(Args):
    minutes: int = Field(1440, ge=1, le=10080)
    limit: int = Field(20, ge=1, le=100)


@tool("neatlogs_alerts", "neatlogs alert history: which alert rules fired, when, threshold, and sample trace IDs to inspect.",
      AlertsArgs, RiskLevel.READ, "neatlogs", targets=lambda a: ["neatlogs:alerts"])
def neatlogs_alerts(a: AlertsArgs) -> ToolOutput:
    ok, d, code = _call("GET", "/alert-history", {"startTime": _iso(a.minutes), "limit": a.limit})
    if not ok:
        return ToolOutput(False, str(d), code)
    keys = ("firedAt", "ruleName", "detectionName", "count", "threshold", "windowMinutes", "sampleTraceIds", "suppressedCount")
    rows = [json.dumps(_pick(x, keys)) for x in d.get("alertHistory", [])]
    return ToolOutput(True, "\n".join(rows) or "(no alerts fired in window)")
