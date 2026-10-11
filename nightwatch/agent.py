"""Agent orchestrator: Claude tool-use loop. The model reads and tests through policy-checked tools,
proposes changes as approval requests, and finishes with a structured report.

The orchestrator never executes arbitrary commands. It dispatches typed tools after a policy decision.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
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
            "ok": {"type": "boolean", "description": "Tool result ok flag, for example true for HTTP 2xx/3xx"},
            "contains": {"type": "string", "description": "Literal, case-sensitive substring (NOT a regex)"},
            "not_contains": {"type": "string", "description": "Literal substring that must be absent"},
            "regex": {"type": "string", "description": "Python regular expression that must match the output"}}}},
}

PROPOSE_TOOL = {
    "name": "propose_action",
    "description": ("Propose ONE infrastructure change for human approval. It does NOT execute. The policy service validates the "
                    "action, binds the approval to the exact parameters and the current state of the target, and returns an approval id. "
                    "Allowed actions: " + "; ".join(f"{a.name} (risk {int(a.risk)}): {a.description} params={list(a.params_model.model_fields)}"
                                                    for a in ACTIONS.values())),
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["action", "params", "root_cause_category", "severity", "expected_effect", "reason", "expected_impact", "risk", "rollback", "verification"],
        "properties": {
            "action": {"type": "string", "enum": sorted(ACTIONS)},
            "root_cause_category": {"type": "string", "enum": ROOT_CAUSE_CATEGORIES, "description": "Category of the root cause that this action addresses"},
            "severity": {"type": "string", "enum": ["P0", "P1", "P2"], "description": "Incident severity; it sets the approval time limit and who approves"},
            "expected_effect": {"type": "string", "enum": ["removes_root_cause", "restores_service_temporarily", "no_effect_on_symptom"],
                                "description": "What this action does to the incident. 'restores_service_temporarily' = a safe mitigation; "
                                               "then also recommend the permanent fix as a manual step. Actions with no effect are rejected."},
            "params": {"type": "object"},
            "reason": {"type": "string", "description": "One sentence, max 18 words: why this fixes the cause"},
            "expected_impact": {"type": "string"},
            "risk": {"type": "string", "description": "One sentence, max 15 words: what can go wrong"},
            "preconditions": {"type": "array", "items": {"type": "string"}},
            "rollback": {"type": "string", "description": "One sentence, max 15 words"},
            "verification": {"type": "array", "items": CHECK_SCHEMA, "minItems": 1,
                             "description": ("Checks that prove the ORIGINAL symptom is gone. Best: repeat the failing request, for example "
                                            "net_http_check with expect {ok: true}. Avoid checks on log lines in short time windows: they age out.")},
        },
    },
}

REPORT_TOOL = {
    "name": "submit_report",
    "description": "Submit the final investigation report. Call it once, at the end. Every claim must reference evidence from tool results.",
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "severity", "problem_statement", "evidence", "hypotheses", "root_cause", "causal_chain", "known_unknowns", "recommendations"],
        "properties": {
            "summary": {"type": "string", "description": "One sentence, max 18 words, ASD-STE100"},
            "severity": {"type": "object", "additionalProperties": False, "required": ["level", "reason"], "properties": {
                "level": {"type": "string", "enum": ["P0", "P1", "P2"]},
                "reason": {"type": "string", "description": "One sentence, max 15 words: the user impact that sets this level"}}},
            "problem_statement": {"type": "object", "additionalProperties": False, "properties": {
                "symptom": {"type": "string", "description": "One sentence, max 15 words"}, "affected_service": {"type": "string"}, "environment": {"type": "string"},
                "scope": {"type": "string"}, "start_time": {"type": "string"}}},
            "timeline": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
                "time": {"type": "string"}, "source": {"type": "string"}, "event": {"type": "string"}}}},
            "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                    "required": ["tool", "finding"], "properties": {
                "tool": {"type": "string"}, "finding": {"type": "string", "description": "One sentence, max 18 words"}, "interpretation": {"type": "string"}}}},
            "hypotheses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
                "cause": {"type": "string"}, "status": {"type": "string", "enum": ["supported", "rejected", "open"]},
                "why": {"type": "string"}}}},
            "root_cause": {"type": "object", "additionalProperties": False, "required": ["category", "resource", "description", "confidence"],
                           "properties": {
                "category": {"type": "string", "enum": ROOT_CAUSE_CATEGORIES},
                "resource": {"type": "string", "description": "The resource at fault, for example nw-redis or deployment/api"},
                "description": {"type": "string", "description": "One sentence, max 18 words"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "symptoms_not_causes": {"type": "array", "items": {"type": "string"}}}},
            "causal_chain": {"type": "array", "minItems": 1, "maxItems": 4, "description": (
                "1-4 links from the ROOT CAUSE to the USER-FACING SYMPTOM, in order. Not the fix, not the follow-up. "
                "Each link needs evidence copied from ONE tool result line or ONE JSON result of this incident, with its "
                "exact values (names, numbers, codes). Two events at the same time are not a link. NightWatch rejects a "
                "link between components with no dependency unless that one line or result names both."),
                "items": {"type": "object", "additionalProperties": False, "required": ["cause", "effect", "evidence"], "properties": {
                    "cause": {"type": "string"}, "effect": {"type": "string"},
                    "evidence": {"type": "string", "description": "Exact text copied from one tool result line"}}}},
            "other_findings": {"type": "array", "maxItems": 10, "description": (
                "For a review of a whole account, cluster or service: every OTHER independent problem that a tool result "
                "proves, ranked by user impact. Each needs an exact evidence quote. Unproven items go to known_unknowns."),
                "items": {"type": "object", "additionalProperties": False, "required": ["problem", "resource", "evidence"], "properties": {
                    "problem": {"type": "string", "description": "One sentence, max 18 words"},
                    "resource": {"type": "string"},
                    "impact": {"type": "string", "enum": ["outage", "degraded", "risk", "cost"]},
                    "evidence": {"type": "string", "description": "Exact text copied from one tool result line"}}}},
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


def validate_report(r: dict) -> list[str]:
    """Deterministic check of submit_report input. Smaller models sometimes send partial or flattened reports."""
    errs = [f"missing field '{k}'" for k in REPORT_TOOL["input_schema"]["required"] if k not in r]
    rc = r.get("root_cause")
    if not isinstance(rc, dict):
        errs.append("root_cause must be an object")
    else:
        for k in ("category", "resource", "description", "confidence"):
            if not rc.get(k):
                errs.append(f"root_cause.{k} is empty")
        if rc.get("category") and rc["category"] not in ROOT_CAUSE_CATEGORIES:
            errs.append(f"root_cause.category must be one of {ROOT_CAUSE_CATEGORIES}")
    sev = r.get("severity")
    if not isinstance(sev, dict) or sev.get("level") not in ("P0", "P1", "P2"):
        errs.append("severity.level must be P0, P1 or P2")
    if not isinstance(r.get("causal_chain"), list) or not r.get("causal_chain"):
        errs.append("causal_chain must list at least one link with an exact evidence quote")
    for k in ("evidence", "hypotheses", "recommendations", "known_unknowns"):
        if k in r and not isinstance(r[k], list):
            errs.append(f"{k} must be an array")
    if isinstance(r.get("evidence"), list) and not r["evidence"]:
        errs.append("evidence is empty: every claim needs a tool result")
    return errs


@dataclass
class RunStats:
    tool_calls: int = 0
    tool_errors: int = 0
    policy_denials: int = 0
    report_rejections: int = 0
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
                 "claude-sonnet-4.6": (3, 15), "claude-haiku": (1, 5), "claude-sonnet-4-6": (3, 15), "claude-opus-5-5": (4, 20),
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
        # 180 s per call (also between stream chunks): a stalled proxy stream fails fast and is retried,
        # instead of blocking the incident for the SDK default of 10 min x 5 attempts.
        client = anthropic.Anthropic(max_retries=2, timeout=180.0, base_url=settings.llm_base_url or "https://api.anthropic.com", **kw)
        self.client = neatlogs.wrap(client, agent="nightwatch", prompt_version=settings.prompt_version) if neatlogs and settings.neatlogs_api_key else client
        self.system = system_prompt(settings.prompt_version)
        from .connectors import enabled_tools
        self._enabled = enabled_tools()
        self.scope: set[str] | None = None     # connectors allowed for the current incident (environment binding)
        self.tools = self._tool_list()

    def _tool_list(self) -> list[dict]:
        tools = [t for t in sorted(self._enabled.values(), key=lambda t: t.name) if self.scope is None or t.connector in self.scope]
        return [t.schema() for t in tools] + [HYPOTHESES_TOOL, PROPOSE_TOOL, REPORT_TOOL]

    def set_scope(self, connectors: set[str] | None) -> None:
        self.scope = connectors
        self.tools = self._tool_list()

    # ----- one tool call through the policy gate -----
    def _dispatch(self, iid: str, block: Any, budget: Budget, stats: RunStats, proposals: list[str]) -> dict:
        name, args = block.name, dict(block.input or {})
        t0 = time.monotonic()
        if name == "update_hypotheses":
            self.audit.log("hypotheses", args, iid)
            self.on_event("hypotheses", args)
            return {"type": "tool_result", "tool_use_id": block.id, "content": "recorded"}
        if name == "propose_action":
            if args.get("expected_effect") not in ("removes_root_cause", "restores_service_temporarily"):
                # Deterministic guard: a change that the agent itself does not expect to fix the cause is noise and risk.
                stats.policy_denials += 1
                self.audit.log("proposal_rejected", {"action": args.get("action"), "params": args.get("params"),
                                                     "reason": "NOT_A_FIX"}, iid)
                self.on_event("tool_denied", {"tool": "propose_action", "code": "NOT_A_FIX",
                                              "reason": "the agent expects no effect on the symptom"})
                return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": "POLICY NOT_A_FIX: propose only an action that removes the root cause or safely restores service. "
                                   "Put manual or follow-up steps in the report recommendations."}
            from .causality import unsupported_pairs
            pairs = unsupported_pairs(str(args.get("reason", "")))
            if pairs:
                stats.policy_denials += 1
                self.audit.log("proposal_rejected", {"action": args.get("action"), "reason": "UNSUPPORTED_CAUSAL_CLAIM", "pairs": pairs}, iid)
                self.on_event("tool_denied", {"tool": "propose_action", "code": "UNSUPPORTED_CAUSAL_CLAIM", "reason": f"{pairs}"})
                return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": f"POLICY UNSUPPORTED_CAUSAL_CLAIM: the reason links {pairs[0][0]} and {pairs[0][1]}, which have no "
                                   "dependency in the inventory. Time correlation is not causation. Remove that claim from the "
                                   "reason, or prove the link first with a tool result that names both, then propose again."}
            urls = re.findall(r"https?://[^\s'\"<>]+", (self.audit.incident(iid) or {}).get("symptom", ""))
            checks = args.get("verification") or []
            if urls and not any(c.get("tool") in ("net_http_check", "net_trace_path") for c in checks):
                stats.policy_denials += 1
                self.audit.log("proposal_rejected", {"action": args.get("action"), "reason": "VERIFICATION_MISSES_SYMPTOM"}, iid)
                self.on_event("tool_denied", {"tool": "propose_action", "code": "VERIFICATION_MISSES_SYMPTOM",
                                              "reason": f"no check repeats the failing request to {urls[0]}"})
                return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": f"POLICY VERIFICATION_MISSES_SYMPTOM: add a verification check that repeats the failing "
                                   f"request (net_http_check or net_trace_path on {urls[0].rstrip('.,')}), then propose again."}
            with _trace("policy.propose", "GUARDRAIL", action=args.get("action")):
                justification = {k: args.get(k) for k in ("root_cause_category", "severity", "expected_effect", "reason", "expected_impact", "risk", "preconditions", "rollback", "verification")}
                dec = self.policy.propose(iid, args.get("action", ""), args.get("params") or {}, justification)
            self.on_event("proposal", {"decision": dec.code, "detail": dec.reason, **args})
            if dec.allowed:
                proposals.append(dec.reason)
                msg = f"{dec.code}: approval id {dec.reason}. The change is NOT executed yet. A human or a standing policy rule decides. Continue to submit_report."
                return {"type": "tool_result", "tool_use_id": block.id, "content": msg}
            stats.policy_denials += 1
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": f"POLICY {dec.code}: {dec.reason}"}

        if self.scope is not None and name in TOOLS and TOOLS[name].connector not in self.scope:
            stats.policy_denials += 1
            self.audit.log("tool_denied", {"tool": name, "args": args, "code": "OUT_OF_ENVIRONMENT"}, iid)
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"POLICY OUT_OF_ENVIRONMENT: {name} does not belong to this incident's environment."}
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
                                     "error_code": out.error_code, "ms": ms, "output": out.text[:12000]}, iid)
        self.on_event("tool_call", {"tool": name, "args": dec.parsed.model_dump(mode="json"), "ok": out.ok, "error_code": out.error_code, "ms": ms,
                                    "preview": out.text[:300]})
        header = f"ok={out.ok}" + (f" error_code={out.error_code}" if out.error_code else "") + f" duration_ms={ms}"
        content = f"{header}\n<tool_output untrusted=\"true\">\n{out.text}\n</tool_output>"
        res = {"type": "tool_result", "tool_use_id": block.id, "content": content}
        if not out.ok:
            res["is_error"] = True
        return res

    def _call_model(self, messages: list[dict], iid: str, deadline_s: float = 240, attempts: int = 2):
        """One model call with a TOTAL time limit. The SDK timeout only measures silence between bytes, and a proxy
        that keeps sending ping events never looks silent. Return the message, or None after the attempts fail."""
        for attempt in range(1, attempts + 1):
            start = time.monotonic()
            try:
                with self.client.messages.stream(
                    model=self.s.model,
                    max_tokens=self.s.max_tokens,
                    system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                    tools=self.tools,
                    messages=_with_tail_breakpoint(messages),
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.s.effort},
                ) as stream:
                    for _event in stream:
                        if time.monotonic() - start > deadline_s:
                            raise TimeoutError(f"model call exceeded {deadline_s:.0f} s")
                    return stream.get_final_message()
            except (TimeoutError, anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.InternalServerError) as e:
                self.audit.log("llm_call_failed", {"attempt": attempt, "error": f"{type(e).__name__}: {str(e)[:200]}",
                                                   "seconds": round(time.monotonic() - start, 1)}, iid)
        return None

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
                resp = self._call_model(messages, iid)
                if resp is None:
                    self.audit.log("llm_unavailable", {"turn": turn}, iid)
                    self.on_event("thought", {"text": "The model did not answer in time (2 attempts). The investigation stops here."})
                    break
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
                    candidate = dict(uses[i].input)
                    errs = validate_report(candidate)
                    if not errs:
                        from .causality import check_chain, split_chain
                        outputs = [f"@@ {e['payload'].get('tool')} {json.dumps(e['payload'].get('args'), sort_keys=True)}\n{e['payload'].get('output', '')}"
                                   for e in self.audit.events(iid) if e["kind"] == "tool_call"]
                        outputs.append("@@ reported_symptom\n" + ((self.audit.incident(iid) or {}).get("symptom") or ""))
                        errs = check_chain(candidate.get("causal_chain") or [], outputs)
                        if errs and stats.report_rejections >= 2:
                            # No retry storm: accept, but never present an unproven link as fact.
                            good, bad = split_chain(candidate.get("causal_chain") or [], outputs)
                            candidate["causal_chain"] = good
                            candidate["unproven_links"] = bad
                            candidate.setdefault("known_unknowns", [])
                            candidate["known_unknowns"] = list(candidate["known_unknowns"]) + [
                                f"Not proven: {l.get('cause')} -> {l.get('effect')}" for l in bad]
                            candidate["root_cause"] = {**candidate.get("root_cause", {}), "confidence": "low"}
                            self.audit.log("report_downgraded", {"unproven_links": bad}, iid)
                            errs = []
                    if errs:
                        stats.report_rejections += 1
                        self.audit.log("report_rejected", {"errors": errs, "causal_chain": candidate.get("causal_chain")}, iid)
                        self.on_event("tool_denied", {"tool": "submit_report", "code": "INVALID_REPORT", "reason": "; ".join(errs)})
                        results[i] = {"type": "tool_result", "tool_use_id": uses[i].id, "is_error": True,
                                      "content": "INVALID_REPORT: " + "; ".join(errs) + ". Send the COMPLETE report again with all required fields as structured JSON."}
                        continue
                    if candidate.get("other_findings"):
                        from .causality import _find
                        outs = [f"@@ {e['payload'].get('tool')} {json.dumps(e['payload'].get('args'), sort_keys=True)}\n{e['payload'].get('output', '')}"
                                for e in self.audit.events(iid) if e["kind"] == "tool_call"]
                        proven = [f for f in candidate["other_findings"] if _find(f.get("evidence", ""), outs) or _find(f.get("problem", ""), outs)]
                        unproven = [f for f in candidate["other_findings"] if f not in proven]
                        candidate["other_findings"] = proven
                        if unproven:
                            candidate["known_unknowns"] = list(candidate.get("known_unknowns") or []) + [f"Not proven: {f.get('problem')}" for f in unproven]
                            self.audit.log("findings_unproven", {"items": unproven}, iid)
                    report = candidate
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
