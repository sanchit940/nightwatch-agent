"""Incident lifecycle: investigate -> propose -> approval -> execute -> verify -> (recover) -> report.

Used by the CLI, the watch loop and the evaluation harness.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from .agent import Agent, InvestigationResult, RunStats
from .audit import Audit
from .config import Settings
from .policy import Policy
from .worker import ExecutionResult, Worker

# An approver decides on a pending approval request: True approve, False reject, None leave pending.
Approver = Callable[[dict], bool | None]
ApprovalMode = Literal["interactive", "wait", "policy-only", "approve-all", "deny-all"]


@dataclass
class IncidentOutcome:
    incident_id: str
    report: dict | None
    executions: list[ExecutionResult] = field(default_factory=list)
    stats: RunStats = field(default_factory=RunStats)
    status: str = "investigated"
    rounds: int = 0


def _merge(a: RunStats, b: RunStats) -> RunStats:
    for k, v in b.__dict__.items():
        if isinstance(v, (int, float)):
            setattr(a, k, getattr(a, k) + v)
        elif isinstance(v, list):
            getattr(a, k).extend(v)
    return a


class IncidentRunner:
    def __init__(self, settings: Settings, audit: Audit, policy: Policy, approver: Approver,
                 on_event: Callable[[str, dict], None] | None = None, approval_wait_s: float = 900,
                 approver_name: str = "human"):
        self.s = settings
        self.audit = audit
        self.policy = policy
        self.approver = approver
        self.on_event = on_event or (lambda k, d: None)
        self.agent = Agent(settings, policy, audit, self.on_event)
        self.worker = Worker(policy, audit)
        self.approval_wait_s = approval_wait_s
        self.approver_name = approver_name

    def _decide(self, aid: str) -> str:
        """Return the final status of an approval request: approved / rejected / expired / pending."""
        deadline = time.monotonic() + self.approval_wait_s
        while True:
            rec = self.audit.approval(aid)
            if rec["status"] != "pending":
                return rec["status"]
            verdict = self.approver(rec)
            if verdict is True:
                self.policy.approve(aid, self.approver_name)
            elif verdict is False:
                self.policy.reject(aid, self.approver_name)
            if verdict is not None:
                return self.audit.approval(aid)["status"]
            if time.monotonic() > deadline or time.time() > rec["expires_at"]:
                return "pending"
            time.sleep(3)

    def handle(self, symptom: str, source: str = "human", dedup_key: str | None = None, max_rounds: int = 3,
               incident_id: str | None = None) -> IncidentOutcome:
        iid = incident_id or self.audit.open_incident(symptom, source, dedup_key)
        self.on_event("incident", {"id": iid, "symptom": symptom})
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        res: InvestigationResult = self.agent.run(iid, f"Incident {iid}. Current time {now}. Source: {source}.\nReported symptom: {symptom}")
        out = IncidentOutcome(iid, res.report, stats=res.stats, rounds=1)
        for _ in range(max_rounds - 1):
            pending = [a for a in res.proposals if self.audit.approval(a)["status"] in ("pending", "approved")]
            if not pending:
                break
            results: list[ExecutionResult] = []
            notes: list[str] = []
            for aid in pending:
                status = self._decide(aid)
                self.on_event("approval", {"id": aid, "status": status})
                if status == "approved":
                    r = self.worker.execute(aid)
                    self.on_event("execution", r.__dict__)
                    results.append(r)
                    out.executions.append(r)
                else:
                    notes.append(f"Approval {aid} is {status}. It was not executed.")
            if not results:
                out.status = "awaiting_approval" if any("pending" in n for n in notes) else "fix_rejected"
                break
            follow = ("Results of the approved actions:\n" + "\n\n".join(r.summary() for r in results) + "\n" + "\n".join(notes) +
                      "\n\nCheck the ORIGINAL symptom again with your own tests. If an action failed or the symptom remains, "
                      "the cause may be different: investigate further and propose another fix only if evidence supports it. "
                      "Then call submit_report again with the resolution field set.")
            res = self.agent.run(iid, follow, messages=res.messages)
            out.report = res.report or out.report
            _merge(out.stats, res.stats)
            out.rounds += 1
        if out.executions:
            v = out.executions[-1].verification.get("status")
            reported = (out.report or {}).get("resolution")
            out.status = reported if reported in ("resolved", "partially_resolved", "unresolved") else (v or "unresolved")
        elif out.status == "investigated" and any(self.audit.approval(a)["status"] == "pending" for a in res.proposals):
            out.status = "awaiting_approval"
        self.audit.set_incident(iid, out.status, out.report)
        return out


def make_approver(mode: ApprovalMode, prompt_fn: Callable[[dict], bool] | None = None) -> Approver:
    if mode == "interactive":
        assert prompt_fn is not None
        return prompt_fn
    if mode == "approve-all":       # evaluation harness acting as the human approver
        return lambda rec: True
    if mode == "deny-all":
        return lambda rec: False
    # "wait": a human approves in another terminal (nightwatch approve <id>); "policy-only": only standing rules.
    return lambda rec: None
