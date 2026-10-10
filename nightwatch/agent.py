"""Agent orchestrator: Claude tool-use loop. The model reads and tests through policy-checked tools,
proposes changes as approval requests, and finishes with a structured report.

The orchestrator never executes arbitrary commands. It dispatches typed tools after a policy decision.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

from .audit import Audit
from .config import Settings
from .connectors import ACTIONS, TOOLS
from .policy import Budget, Policy
from .prompts import system_prompt

try:
    import neatlogs
except ImportError:  # pragma: no cover
    neatlogs = None

ROOT_CAUSE_CATEGORIES = [
    "dependency_down", "proxy_misconfig", "memory_exhaustion", "cpu_saturation", "disk_full", "dns_failure",
    "network_partition", "config_error", "image_pull_failure", "probe_misconfig", "scheduling_failure",
    "scaled_to_zero", "certificate_error", "permission_error", "application_bug", "other", "unknown",
]


def _trace(name: str, kind: str, **attrs):
    if neatlogs is None:
        return contextlib.nullcontext()
    return neatlogs.trace(name, kind=kind, **attrs)


# ---------- meta tools (handled by the orchestrator, not by connectors) ----------

HYPOTHESES_TOOL = {
    "name": "update_hypotheses",
    "description": "Record the current list of hypotheses with evidence for/against, status and the next test. Call it after each significant finding. It does not run anything.",
    "input_schema": {
        "type": "object", "additionalProperties": False, "required": ["hypotheses"],
        "properties": {"hypotheses": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "cause", "status"],
            "properties": {
                "id": {"type": "string"}, "cause": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "supported", "rejected"]},
                "evidence_for": {"type": "array", "items": {"type": "string"}},
                "evidence_against": {"type": "array", "items": {"type": "string"}},
                "next_test": {"type": "string"}}}}},
    },
}

CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["tool", "args"],
    "properties": {
        "tool": {"type": "string", "description": "A read-only or diagnostic tool name, for example net_http_check"},
        "args": {"type": "object", "description": "Arguments for that tool"},
        "expect": {"type": "object", "additionalProperties": False, "properties": {
            "ok": {"type": "boolean"}, "contains": {"type": "string"}, "not_contains": {"type": "string"}}}},
}

PROPOSE_TOOL = {
    "name": "propose_action",
    "description": ("Propose ONE infrastructure change for human approval. It does NOT execute. The policy service validates the "
                    "action, binds the approval to the exact parameters and the current state of the target, and returns an approval id. "
                    "Allowed actions: " + "; ".join(f"{a.name} (risk {int(a.risk)}): {a.description} params={list(a.params_model.model_fields)}"
                                                    for a in ACTIONS.values())),
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["action", "params", "reason", "expected_impact", "risk", "rollback", "verification"],
        "properties": {
            "action": {"type": "string", "enum": sorted(ACTIONS)},
            "params": {"type": "object"},
            "reason": {"type": "string", "description": "Why this fixes the root cause, with evidence references"},
            "expected_impact": {"type": "string"},
            "risk": {"type": "string", "description": "What can go wrong"},
            "preconditions": {"type": "array", "items": {"type": "string"}},
            "rollback": {"type": "string"},
            "verification": {"type": "array", "items": CHECK_SCHEMA, "minItems": 1,
                             "description": "Checks that prove the ORIGINAL symptom is gone, for example the failing HTTP request now returns 200"},
        },
    },
}

REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit the final investigation report. Call it once, at the end. Every claim must reference evidence from tool results.",
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "problem_statement", "evidence", "hypotheses", "root_cause", "known_unknowns", "recommendations"],
        "properties": {
            "summary": {"type": "string", "description": "One or two sentences"},
            "problem_statement": {"type": "object", "additionalProperties": False, "properties": {
                "symptom": {"type": "string"}, "affected_service": {"type": "string"}, "environment": {"type": "string"},
                "scope": {"type": "string"}, "start_time": {"type": "string"}}},
            "timeline": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
                "time": {"type": "string"}, "source": {"type": "string"}, "event": {"type": "string"}}}},
            "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                    "required": ["tool", "finding"], "properties": {
                "tool": {"type": "string"}, "finding": {"type": "string"}, "interpretation": {"type": "string"}}}},
            "hypotheses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
                "cause": {"type": "string"}, "status": {"type": "string", "enum": ["supported", "rejected", "open"]},
                "why": {"type": "string"}}}},
            "root_cause": {"type": "object", "additionalProperties": False, "required": ["category", "resource", "description", "confidence"],
                           "properties": {
                "category": {"type": "string", "enum": ROOT_CAUSE_CATEGORIES},
                "resource": {"type": "string", "description": "The resource at fault, for example nw-redis or deployment/api"},
                "description": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "symptoms_not_causes": {"type": "array", "items": {"type": "string"}}}},
            "known_unknowns": {"type": "array", "items": {"type": "string"}},
            "recommendations": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
                "description": {"type": "string"}, "approval_id": {"type": "string"},
                "type": {"type": "string", "enum": ["proposed_action", "manual", "follow_up"]}}}},
            "resolution": {"type": "string", "enum": ["not_attempted", "resolved", "partially_resolved", "unresolved"]},
        },
    },
}


def _with_tail_breakpoint(messages: list[dict]) -> list[dict]:
    """Copy of messages with one cache breakpoint on the newest block, so each turn reads the previous prefix
    from cache. Explicit breakpoints are used because some proxies drop the top-level cache_control field.
    The stored history is not changed, so old breakpoints do not pile up (the API allows 4)."""
    if not messages:
        return messages
    last = messages[-1]
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(b) if isinstance(b, dict) else b for b in content]
    if blocks and isinstance(blocks[-1], dict):
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return messages[:-1] + [{**last, "content": blocks}]


@dataclass
class RunStats:
    tool_calls: int = 0
    tool_errors: int = 0
    policy_denials: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0
    tools_used: list[str] = field(default_factory=list)

    def cost_usd(self, model: str) -> float:
        # $ per million tokens (input, output). Cache reads 10% of input; cache writes 125%.
        price = {"claude-opus-4-8": (5, 25), "claude-opus-4.6": (5, 25), "claude-opus-4-6": (5, 25),
                 "claude-sonnet-4.6": (3, 15), "claude-sonnet-4-6": (3, 15), "claude-opus-5-5": (4, 20),
                 "claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.1, 0.5)}
        i, o = price.get(model, (5, 25))
        return round((self.input_tokens * i + self.output_tokens * o + self.cache_read_tokens * i * 0.1
                      + self.cache_write_tokens * i * 1.25) / 1e6, 4)


@dataclass
class InvestigationResult:
    incident_id: str
    report: dict | None
    proposals: list[str]
    stats: RunStats
    messages: list[dict]


class Agent:
    def __init__(self, settings: Settings, policy: Policy, audit: Audit,
                 on_event: Callable[[str, dict], None] | None = None):
        self.s = settings
        self.policy = policy
        self.audit = audit
        self.on_event = on_event or (lambda kind, data: None)
        kw = {"api_key": settings.llm_api_key} if settings.llm_api_key else {}
        client = anthropic.Anthropic(max_retries=4, base_url=settings.llm_base_url or "https://api.anthropic.com", **kw)
        self.client = neatlogs.wrap(client, agent="nightwatch", prompt_version=settings.prompt_version) if neatlogs and settings.neatlogs_api_key else client
        self.system = system_prompt(settings.prompt_version)
        self.tools = [t.schema() for t in sorted(TOOLS.values(), key=lambda t: t.name)] + [HYPOTHESES_TOOL, PROPOSE_TOOL, REPORT_TOOL]

    # ----- one tool call through the policy gate -----
    def _dispatch(self, iid: str, block: Any, budget: Budget, stats: RunStats, proposals: list[str]) -> dict:
        name, args = block.name, dict(block.input or {})
        t0 = time.monotonic()
        if name == "update_hypotheses":
            self.audit.log("hypotheses", args, iid)
            self.on_event("hypotheses", args)
            return {"type": "tool_result", "tool_use_id": block.id, "content": "recorded"}
        if name == "propose_action":
            with _trace("policy.propose", "GUARDRAIL", action=args.get("action")):
                justification = {k: args.get(k) for k in ("reason", "expected_impact", "risk", "preconditions", "rollback", "verification")}
                dec = self.policy.propose(iid, args.get("action", ""), args.get("params") or {}, justification)
            self.on_event("proposal", {"decision": dec.code, "detail": dec.reason, **args})
            if dec.allowed:
                proposals.append(dec.reason)
                msg = f"{dec.code}: approval id {dec.reason}. The change is NOT executed yet. A human or a standing policy rule decides. Continue to submit_report."
                return {"type": "tool_result", "tool_use_id": block.id, "content": msg}
            stats.policy_denials += 1
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": f"POLICY {dec.code}: {dec.reason}"}

        with _trace("policy.check_tool", "GUARDRAIL", tool_name=name):
            dec = self.policy.check_tool(name, args, budget)
        if not dec.allowed:
            stats.policy_denials += 1
            budget.denied += 1
            self.audit.log("tool_denied", {"tool": name, "args": args, "code": dec.code, "reason": dec.reason}, iid)
            self.on_event("tool_denied", {"tool": name, "code": dec.code, "reason": dec.reason})
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": f"POLICY {dec.code}: {dec.reason}"}
        budget.tool_calls += 1
        stats.tool_calls += 1
        stats.tools_used.append(name)
        tool = TOOLS[name]
        with _trace(f"tool:{name}", "TOOL", tool_name=name, risk=int(tool.risk), connector=tool.connector):
            try:
                out = tool.fn(dec.parsed)
            except Exception as e:  # noqa: BLE001 - a connector bug must not kill the investigation
                from .connectors.base import ToolOutput
                out = ToolOutput(False, f"{type(e).__name__}: {e}", "CONNECTOR_ERROR")
        ms = int((time.monotonic() - t0) * 1000)
        if not out.ok:
            stats.tool_errors += 1
        self.audit.log("tool_call", {"tool": name, "args": dec.parsed.model_dump(mode="json"), "risk": int(tool.risk), "ok": out.ok,
                                     "error_code": out.error_code, "ms": ms, "output": out.text[:4000]}, iid)
        self.on_event("tool_call", {"tool": name, "args": dec.parsed.model_dump(mode="json"), "ok": out.ok, "error_code": out.error_code, "ms": ms,
                                    "preview": out.text[:300]})
        header = f"ok={out.ok}" + (f" error_code={out.error_code}" if out.error_code else "") + f" duration_ms={ms}"
        content = f"{header}\n<tool_output untrusted=\"true\">\n{out.text}\n</tool_output>"
        res = {"type": "tool_result", "tool_use_id": block.id, "content": content}
        if not out.ok:
            res["is_error"] = True
        return res

    # ----- the loop -----
    def run(self, iid: str, user_message: str, messages: list[dict] | None = None, max_turns: int = 30) -> InvestigationResult:
        stats = RunStats()
        budget = self.policy.new_budget()
        proposals: list[str] = []
        report: dict | None = None
        messages = list(messages or [])
        messages.append({"role": "user", "content": user_message})
        started = time.monotonic()
        root = _trace("investigation", "AGENT", session_id=iid, incident_id=iid, prompt_version=self.s.prompt_version, model=self.s.model)
        with root:
            for turn in range(max_turns):
                with self.client.messages.stream(
                    model=self.s.model,
                    max_tokens=self.s.max_tokens,
                    system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                    tools=self.tools,
                    messages=_with_tail_breakpoint(messages),
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.s.effort},
                ) as stream:
                    resp = stream.get_final_message()
                stats.llm_calls += 1
                u = resp.usage
                stats.input_tokens += u.input_tokens
                stats.output_tokens += u.output_tokens
                stats.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
                stats.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
                messages.append({"role": "assistant", "content": resp.content})
                for b in resp.content:
                    if b.type == "text" and b.text.strip():
                        self.on_event("thought", {"text": b.text})
                if resp.stop_reason == "refusal":
                    self.audit.log("model_refusal", {"turn": turn}, iid)
                    break
                uses = [b for b in resp.content if b.type == "tool_use"]
                if not uses:
                    if report is None and resp.stop_reason in ("end_turn", "max_tokens"):
                        messages.append({"role": "user", "content": "Submit the final report now with submit_report."})
                        continue
                    break
                results: list[dict] = [None] * len(uses)  # type: ignore[list-item]
                report_idx = [i for i, b in enumerate(uses) if b.name == "submit_report"]
                for i in report_idx:
                    report = dict(uses[i].input)
                    self.audit.log("report", report, iid)
                    self.on_event("report", report)
                    results[i] = {"type": "tool_result", "tool_use_id": uses[i].id, "content": "report accepted"}
                others = [i for i in range(len(uses)) if i not in report_idx]
                # Read-only tools are parallel-safe; proposals are cheap. Run them concurrently.
                with ThreadPoolExecutor(max_workers=6) as pool:
                    # copy_context keeps each tool span under the investigation span in the neatlogs trace
                    futs = {i: pool.submit(contextvars.copy_context().run, self._dispatch, iid, uses[i], budget, stats, proposals)
                            for i in others}
                    for i, f in futs.items():
                        results[i] = f.result()
                messages.append({"role": "user", "content": results})
                if report is not None:
                    break
        stats.seconds = round(time.monotonic() - started, 1)
        self.audit.log("run_stats", {**stats.__dict__, "cost_usd": stats.cost_usd(self.s.model), "model": self.s.model,
                                     "prompt_version": self.s.prompt_version}, iid)
        return InvestigationResult(iid, report, proposals, stats, messages)
