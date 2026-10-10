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
                 approver_name: str = "human", notifier=None, guidance_wait_s: float = 0):
        self.s = settings
        self.audit = audit
        self.policy = policy
        self.approver = approver
        self.on_event = on_event or (lambda k, d: None)
        self.agent = Agent(settings, policy, audit, self.on_event)
        self.worker = Worker(policy, audit)
        self.approval_wait_s = approval_wait_s
        self.approver_name = approver_name
        self.notifier = notifier                 # SlackNotifier or None
        self.guidance_wait_s = guidance_wait_s   # how long to wait for a human instruction when no action can fix it

    # ----- decisions -----
    def _take_guidance(self, iid: str, aid: str | None = None) -> list[dict]:
        g = [x for x in self.audit.pending_guidance(iid) if aid is None or x["approval_id"] in (aid, None)]
        for x in g:
            self.audit.consume_guidance(x["id"])
        return g

    def _decide(self, aid: str, iid: str) -> tuple[str, list[dict]]:
        """Final status of an approval request: approved / rejected / expired / pending / guidance (+ instructions)."""
        deadline = time.monotonic() + self.approval_wait_s
        while True:
            rec = self.audit.approval(aid)
            g = self._take_guidance(iid, aid)
            if g:
                if rec["status"] == "pending":
                    self.policy.reject(aid, g[0]["user"], "replaced by an approver instruction")
                return "guidance", g
            if rec["status"] != "pending":
                return rec["status"], []
            verdict = self.approver(rec)
            if verdict is True:
                self.policy.approve(aid, self.approver_name)
            elif verdict is False:
                self.policy.reject(aid, self.approver_name)
            if verdict is not None:
                return self.audit.approval(aid)["status"], []
            if time.time() > rec["expires_at"]:
                self.audit.update_approval(aid, status="expired")
                self.audit.log("approval_expired", {"id": aid}, iid)
                return "expired", []
            if time.monotonic() > deadline:
                return "pending", []
            time.sleep(2)

    def _wait_guidance(self, iid: str) -> list[dict]:
        deadline = time.monotonic() + self.guidance_wait_s
        while time.monotonic() < deadline:
            g = self._take_guidance(iid)
            if g:
                return g
            time.sleep(2)
        return []

    # ----- notifications -----
    def _notify_round(self, iid: str, report: dict | None, proposals: list[str]) -> None:
        if not self.notifier:
            return
        report = report or {"summary": "Investigation ended without a complete report.", "severity": {"level": "P1", "reason": "unknown"}}
        new = [self.audit.approval(a) for a in proposals]
        new = [r for r in new if r and r["status"] == "pending"]
        try:
            if new:
                for rec in new:
                    self.notifier.post_plan(iid, report, rec)
            else:
                needs_human = report.get("resolution") not in ("resolved",) and self.guidance_wait_s > 0
                self.notifier.post_report(iid, report, needs_human)
        except Exception as e:  # noqa: BLE001 - a Slack outage must not stop the incident
            self.audit.log("notify_failed", {"error": f"{type(e).__name__}: {e}"}, iid)

    def _notify(self, iid: str, text: str) -> None:
        if self.notifier:
            try:
                self.notifier.post_update(iid, text)
            except Exception as e:  # noqa: BLE001
                self.audit.log("notify_failed", {"error": f"{type(e).__name__}: {e}"}, iid)

    # ----- lifecycle -----
    def handle(self, symptom: str, source: str = "human", dedup_key: str | None = None, max_rounds: int = 3,
               incident_id: str | None = None) -> IncidentOutcome:
        from .notify.slack import describe_action, execution_message
        iid = incident_id or self.audit.open_incident(symptom, source, dedup_key)
        self.on_event("incident", {"id": iid, "symptom": symptom})
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        res: InvestigationResult = self.agent.run(iid, f"Incident {iid}. Current time {now}. Source: {source}.\nReported symptom: {symptom}")
        out = IncidentOutcome(iid, res.report, stats=res.stats, rounds=1)
        self._notify_round(iid, out.report, res.proposals)
        while out.rounds < max_rounds:
            pending = [a for a in res.proposals if self.audit.approval(a)["status"] in ("pending", "approved")]
            results: list[ExecutionResult] = []
            notes: list[str] = []
            guidance: list[dict] = []
            if pending:
                for aid in pending:
                    status, g = self._decide(aid, iid)
                    self.on_event("approval", {"id": aid, "status": status})
                    rec = self.audit.approval(aid)
                    if status == "approved":
                        r = self.worker.execute(aid)
                        self.on_event("execution", r.__dict__)
                        self._notify(iid, execution_message(describe_action(rec["action"], rec["params"]), r.__dict__)["text"])
                        results.append(r)
                        out.executions.append(r)
                    elif status == "guidance":
                        guidance += g
                        notes.append(f"Approval {aid} was replaced by an approver instruction. It was NOT executed.")
                    else:
                        notes.append(f"Approval {aid} is {status}. It was not executed.")
                        if self.notifier:
                            if status == "expired":
                                self.notifier.close_buttons(iid, aid, "⌛ The approval expired. No change was made.")
                            self._notify(iid, f"Approval `{aid}` is {status}. No change was made.")
            elif self.notifier and self.guidance_wait_s > 0 and (out.report or {}).get("resolution") != "resolved":
                guidance = self._wait_guidance(iid)
            if not results and not guidance:
                if any("pending" in n for n in notes):
                    out.status = "awaiting_approval"
                elif notes:
                    out.status = "fix_rejected"
                break
            parts = []
            if results:
                parts.append("Results of the approved actions:\n" + "\n\n".join(r.summary() for r in results))
            if notes:
                parts.append("\n".join(notes))
            for g in guidance:
                parts.append(f"APPROVER INSTRUCTION from {g['user']}:\n{g['text']}\n"
                             "Follow this instruction within the policy. Test what it asks first. If it needs a change, call "
                             "propose_action (a new approval is needed). If the approver says they changed something by hand, "
                             "verify the original symptom.")
            parts.append("Check the ORIGINAL symptom again with your own tests. If an action failed or the symptom remains, "
                         "the cause may be different: investigate further and propose another fix only if evidence supports it. "
                         "Then call submit_report again with the resolution field set.")
            res = self.agent.run(iid, "\n\n".join(parts), messages=res.messages)
            out.report = res.report or out.report
            _merge(out.stats, res.stats)
            out.rounds += 1
            self._notify_round(iid, out.report, res.proposals)
        if out.status in ("investigated",) or out.executions:
            reported = (out.report or {}).get("resolution")
            if reported in ("resolved", "partially_resolved", "unresolved"):
                out.status = reported
            elif out.executions:
                out.status = out.executions[-1].verification.get("status") or "unresolved"
            elif any(self.audit.approval(a)["status"] == "pending" for a in res.proposals):
                out.status = "awaiting_approval"
        self.audit.set_incident(iid, out.status, out.report)
        label = {"resolved": "✅ RESOLVED", "partially_resolved": "⚠️ PARTLY RESOLVED", "unresolved": "❌ NOT RESOLVED",
                 "awaiting_approval": "⏳ WAITING FOR APPROVAL", "fix_rejected": "❌ FIX REJECTED: a human must act",
                 "investigated": "🧭 INVESTIGATED: no change proposed"}.get(out.status, out.status)
        self._notify(iid, f"*Incident status: {label}.* Rounds: {out.rounds}. Tool calls: {out.stats.tool_calls}. "
                          f"Audit: `nightwatch audit --incident {iid}`.")
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
