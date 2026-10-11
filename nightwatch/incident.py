"""Incident lifecycle: investigate -> propose -> approval -> execute -> verify -> (recover) -> report.

Used by the CLI, the watch loop and the evaluation harness.
"""

from __future__ import annotations

import re
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
        self._last_report: dict | None = None

    # ----- decisions -----
    def _take_guidance(self, iid: str, aid: str | None = None) -> list[dict]:
        g = [x for x in self.audit.pending_guidance(iid) if aid is None or x["approval_id"] in (aid, None)]
        for x in g:
            self.audit.consume_guidance(x["id"])
        return g

    def _decide(self, aid: str, iid: str) -> tuple[str, list[dict]]:
        """Final status of an approval request: approved / rejected / expired / pending / guidance (+ instructions)."""
        deadline = time.monotonic() + self.approval_wait_s
        reminded = False
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
            if self.notifier and not reminded:
                frac = float(self.policy.escalation().get("remind_at_fraction", 0.5))
                if time.time() >= rec["created_at"] + frac * (rec["expires_at"] - rec["created_at"]):
                    reminded = True
                    self._notify(iid, f"⏰ Plan `{aid}` waits for a decision. It expires at "
                                      f"{time.strftime('%H:%M UTC', time.gmtime(rec['expires_at']))}. {self._approver_mentions(rec)}")
            if time.time() > rec["expires_at"]:
                self.audit.update_approval(aid, status="expired")
                self.audit.log("approval_expired", {"id": aid}, iid)
                return "expired", []
            if time.monotonic() > deadline:
                return "pending", []
            time.sleep(2)

    def _approver_mentions(self, rec: dict, escalated: bool = False) -> str:
        sev = (rec.get("justification") or {}).get("severity") or "P1"
        slack = self.policy.cfg.get("slack") or {}
        people = list((slack.get("approvers") or {}).get(sev) or [])
        if escalated:
            people += ((slack.get("escalation") or {}).get("contacts") or {}).get(sev) or []
        return " ".join(f"<@{u}>" for u in people) or ("<!channel>" if escalated else "")

    def _escalate(self, iid: str, aid: str) -> str | None:
        """Nobody decided in time. Re-issue the same plan (bound to the state NOW) and mention the escalation contacts.
        Return the new approval id, or None when the incident must go back to the agent or to a human."""
        from .connectors import ACTIONS
        rec = self.audit.approval(aid)
        j = dict(rec.get("justification") or {})
        level = int(j.get("escalation_level", 0))
        max_levels = int(self.policy.escalation().get("max_levels", 0))
        if level >= max_levels:
            self.notifier.close_buttons(iid, aid, "⌛ Expired without a decision. No change was made.")
            self._notify(iid, f"⛔ No decision after {level} escalations. No change made. A human must act. "
                              f"{self._approver_mentions(rec, escalated=True)}")
            return None
        act = ACTIONS[rec["action"]]
        current = act.fingerprint(act.params_model.model_validate(rec["params"]))
        if current != rec["fingerprint"]:
            self.notifier.close_buttons(iid, aid, "⌛ Expired. The target changed after the plan. NightWatch checks again.")
            return None   # the round notes tell the agent to re-investigate
        j["escalation_level"] = level + 1
        d = self.policy.propose(iid, rec["action"], rec["params"], j)
        if not d.allowed:
            return None
        self.audit.log("approval_escalated", {"expired": aid, "new": d.reason, "level": level + 1}, iid)
        self.notifier.close_buttons(iid, aid, f"⌛ Expired without a decision. Escalated as plan `{d.reason}`.")
        self._notify(iid, f"🚨 Escalation {level + 1} of {max_levels}. Nobody decided in time. "
                          f"{self._approver_mentions(rec, escalated=True)} Decide on the new plan below.")
        self.notifier.post_plan(iid, self._last_report or {}, self.audit.approval(d.reason))
        return d.reason

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
            elif report.get("resolution") != "resolved" and self.guidance_wait_s > 0:
                self.notifier.post_human_needed(iid, report)
            # Resolved, or nothing to decide: the single final message at the end of handle() covers it.
        except Exception as e:  # noqa: BLE001 - a Slack outage must not stop the incident
            self.audit.log("notify_failed", {"error": f"{type(e).__name__}: {e}"}, iid)

    def _notify(self, iid: str, text: str) -> None:
        if self.notifier:
            try:
                self.notifier.post_update(iid, text)
            except Exception as e:  # noqa: BLE001
                self.audit.log("notify_failed", {"error": f"{type(e).__name__}: {e}"}, iid)

    @staticmethod
    def _who(user: str | None) -> str:
        u = user or "unknown"
        return f"<@{u.split(':', 1)[1]}>" if u.startswith("slack:") else u

    def _instruction_text(self, g: dict, replaced: str | None) -> str:
        quoted = "\n".join(f"> {l}" for l in g["text"].splitlines())
        rep = f"Plan `{replaced}` is cancelled. " if replaced else ""
        return f"✍️ {self._who(g['user'])}:\n{quoted}\n{rep}NightWatch checks your instruction now."

    def _announce_decision(self, iid: str, rec: dict, status: str, guidance: list[dict]) -> None:
        """One place that tells Slack about every decision, whatever its source (Slack button, CLI, policy rule)."""
        if not self.notifier:
            return
        from_slack = str(rec.get("approver") or "").startswith("slack:")
        if status == "guidance":
            for g in guidance:
                self._notify(iid, self._instruction_text(g, rec["id"]))
            if not str(guidance[0]["user"]).startswith("slack:"):
                self.notifier.close_buttons(iid, rec["id"], f"✍️ Cancelled by an instruction from {self._who(guidance[0]['user'])}. Not executed.")
        elif status == "approved" and not from_slack:
            self.notifier.close_buttons(iid, rec["id"], f"✅ Approved by {self._who(rec.get('approver'))}.")
        elif status == "rejected" and not from_slack:
            self.notifier.close_buttons(iid, rec["id"], f"❌ Rejected by {self._who(rec.get('approver'))}. No change made.")

    # ----- environment binding -----
    ENV_CONNECTORS = {
        # No network tools: private VPC addresses are not reachable from the agent host (run 2: 7 timeouts, about 35 s).
        "aws": {"aws", "knowledge"},
        "lab": {"docker", "kubernetes", "linux", "network", "monitoring", "grafana", "loki", "knowledge"},
        "ai-agent": {"neatlogs", "network", "knowledge"},
        "ci": {"ci", "knowledge"},
    }

    @staticmethod
    def infer_environment(symptom: str) -> str:
        """Deterministic: which environment an incident belongs to. Lab incidents never see the company AWS tools."""
        low = symptom.lower()
        if re.search(r"\b(aws|amazon|ec2|eks|alb|nlb|elb|cloudwatch|cloudtrail|rds|s3)\b|\b[a-z]{2}-(south|north|east|west|central|southeast|northeast)-\d\b", low) \
                and not re.search(r"localhost|\bnw-|\blab\b(?! account)|kind-nightwatch", low.replace("not the local lab", "")):
            return "aws"
        if re.search(r"\b(jenkins|github actions?|gitlab|pipeline|workflow run|ci/cd|ci job|build (is |has )?(fail|broken|red)|builds? fail)", low):
            return "ci"
        if re.search(r"\b(ai agent|llm|trace|neatlogs)\b", low):
            return "ai-agent"
        return "lab"

    def environment_for(self, symptom: str, explicit: str | None = None) -> tuple[str, set[str]]:
        """Environment and its connectors. policy.yaml can redefine environments (`environments`) and the
        environment for symptoms that name no platform (`default_environment`, for example aws on a cloud install)."""
        cfg = self.policy.cfg
        envs = {**self.ENV_CONNECTORS, **{k: set(v or []) for k, v in (cfg.get("environments") or {}).items()}}
        env = explicit or self.infer_environment(symptom)
        if not explicit and env == "lab" and cfg.get("default_environment"):
            env = cfg["default_environment"]
        # An unknown environment gets only the knowledge tools: never every tool.
        return env, envs.get(env, {"knowledge"})

    # ----- lifecycle -----
    def handle(self, symptom: str, source: str = "human", dedup_key: str | None = None, max_rounds: int = 3,
               incident_id: str | None = None, environment: str | None = None) -> IncidentOutcome:
        from .notify.slack import describe_action, execution_message
        iid = incident_id or self.audit.open_incident(symptom, source, dedup_key)
        self.on_event("incident", {"id": iid, "symptom": symptom})
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        env, connectors = self.environment_for(symptom, environment)
        self.audit.set_meta(iid, "environment", env)
        self.audit.log("environment", {"environment": env, "connectors": sorted(connectors)}, iid)
        self.agent.set_scope(connectors)
        res: InvestigationResult = self.agent.run(iid, f"Incident {iid}. Current time {now}. Source: {source}. Environment: {env}.\nReported symptom: {symptom}")
        out = IncidentOutcome(iid, res.report, stats=res.stats, rounds=1)
        self._last_report = out.report
        self._notify_round(iid, out.report, res.proposals)
        while out.rounds < max_rounds:
            pending = [a for a in res.proposals if self.audit.approval(a)["status"] in ("pending", "approved")]
            results: list[ExecutionResult] = []
            notes: list[str] = []
            guidance: list[dict] = []
            if pending:
                for aid in pending:
                    status, g = self._decide(aid, iid)
                    while status == "expired" and self.notifier:
                        new_aid = self._escalate(iid, aid)
                        if not new_aid:
                            break
                        aid = new_aid
                        status, g = self._decide(aid, iid)
                    self.on_event("approval", {"id": aid, "status": status})
                    rec = self.audit.approval(aid)
                    self._announce_decision(iid, rec, status, g)
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
                            if status != "expired":   # _escalate already reported the expiry
                                self._notify(iid, f"Plan `{aid}` is {status}. No change made.")
            elif self.notifier and self.guidance_wait_s > 0 and (out.report or {}).get("resolution") != "resolved":
                guidance = self._wait_guidance(iid)
                for g in guidance:
                    self._notify(iid, self._instruction_text(g, None))
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
            self._last_report = out.report
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
        if self.notifier:
            try:
                self.notifier.post_final(iid, out.report or {"summary": "No complete report."}, out.status)
            except Exception as e:  # noqa: BLE001
                self.audit.log("notify_failed", {"error": f"{type(e).__name__}: {e}"}, iid)
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
