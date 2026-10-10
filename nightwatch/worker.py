"""Restricted action worker and verification engine.

The worker executes ONLY an approval that the policy service authorizes at execution time.
The verification engine re-runs read-only checks and decides resolved / partial / unresolved deterministically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .audit import Audit
from .connectors.base import ACTIONS, TOOLS, RiskLevel
from .policy import Policy

try:
    import neatlogs
except ImportError:  # pragma: no cover
    neatlogs = None


def _trace(name: str, kind: str, **attrs):
    import contextlib
    if neatlogs is None:
        return contextlib.nullcontext()
    return neatlogs.trace(name, kind=kind, **attrs)


@dataclass
class ExecutionResult:
    approval_id: str
    executed: bool
    ok: bool
    code: str
    detail: str
    verification: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        v = self.verification
        vtxt = f"\nVerification: {v.get('status')} ({v.get('passed')}/{v.get('total')} checks passed)\n{v.get('details', '')}" if v else ""
        note = ("" if self.executed else
                "\nNightWatch did NOT execute this action. Any change to the target was made by someone or something else.")
        return f"Approval {self.approval_id}: executed={self.executed} ok={self.ok} code={self.code}\n{self.detail[:1500]}{note}{vtxt}"


def run_check(check: dict) -> tuple[bool, str]:
    """One verification check: {tool, args, expect: {ok?, contains?, not_contains?}}. Read-only tools only."""
    tool = TOOLS.get(check.get("tool", ""))
    if tool is None or tool.risk > RiskLevel.DIAGNOSTIC:
        return False, f"invalid verification tool {check.get('tool')}"
    try:
        args = tool.input_model.model_validate(check.get("args", {}))
    except Exception as e:  # noqa: BLE001
        return False, f"invalid args: {e}"[:300]
    out = tool.fn(args)
    exp = check.get("expect") or {"ok": True}
    passed = True
    if "ok" in exp and bool(out.ok) != bool(exp["ok"]):
        passed = False
    if exp.get("contains") and exp["contains"] not in out.text:
        passed = False
    if exp.get("not_contains") and exp["not_contains"] in out.text:
        passed = False
    if exp.get("regex"):
        import re
        try:
            if not re.search(exp["regex"], out.text):
                passed = False
        except re.error as e:
            return False, f"invalid regex: {e}"
    return passed, out.text[:600]


def verify(checks: list[dict], wait_s: float = 60, interval_s: float = 5) -> dict[str, Any]:
    """Poll the checks until all pass or the wait time ends. A recovering service needs time."""
    if not checks:
        return {"status": "unverified", "passed": 0, "total": 0, "details": "no verification checks were defined"}
    deadline = time.monotonic() + wait_s
    best: list = []
    best_passed = -1
    while True:
        results = [(c, *run_check(c)) for c in checks]
        n = sum(1 for _, ok, _ in results if ok)
        # Report the best round: a check that passed early (for example a startup log line) can age out later.
        if n > best_passed:
            best, best_passed = results, n
        if n == len(checks) or time.monotonic() > deadline:
            break
        time.sleep(interval_s)
    results, passed = best, best_passed
    status = "resolved" if passed == len(checks) else ("partially_resolved" if passed else "unresolved")
    details = "\n".join(f"- [{'PASS' if ok else 'FAIL'}] {c['tool']} {c.get('args')} expect={c.get('expect')}: {txt[:300]}"
                        for c, ok, txt in results)
    return {"status": status, "passed": passed, "total": len(checks), "details": details}


class Worker:
    def __init__(self, policy: Policy, audit: Audit):
        self.policy = policy
        self.audit = audit

    def execute(self, approval_id: str) -> ExecutionResult:
        rec = self.audit.approval(approval_id)
        iid = rec["incident_id"] if rec else None
        with _trace("policy.authorize", "GUARDRAIL", approval_id=approval_id):
            dec = self.policy.authorize(approval_id)
        if not dec.allowed:
            return ExecutionResult(approval_id, False, False, dec.code, dec.reason)
        act = ACTIONS[rec["action"]]
        self.audit.update_approval(approval_id, status="executing", executed_at=time.time())
        self.audit.log("action_started", {"id": approval_id, "action": rec["action"], "params": rec["params"]}, iid)
        with _trace(f"action:{rec['action']}", "TOOL", tool_name=rec["action"], approval_id=approval_id):
            try:
                res = act.execute(dec.parsed)
            except Exception as e:  # noqa: BLE001
                res = None
                err = f"{type(e).__name__}: {e}"
        if res is None:
            self.audit.update_approval(approval_id, status="executed", result={"ok": False, "error": err})
            self.audit.log("action_failed", {"id": approval_id, "error": err}, iid)
            return ExecutionResult(approval_id, True, False, "EXCEPTION", err)
        self.audit.update_approval(approval_id, status="executed", result={"ok": res.ok, "detail": res.detail[:2000], "error_code": res.error_code})
        self.audit.log("action_finished", {"id": approval_id, "ok": res.ok, "error_code": res.error_code, "detail": res.detail[:2000]}, iid)
        out = ExecutionResult(approval_id, True, res.ok, res.error_code or "OK", res.detail)
        checks = (rec.get("justification") or {}).get("verification", [])
        with _trace("verify", "CHAIN", approval_id=approval_id):
            out.verification = verify(checks, wait_s=0 if not res.ok else 60)
        self.audit.log("verification", {"id": approval_id, **out.verification}, iid)
        return out
