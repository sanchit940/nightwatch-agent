"""Policy and approval service. Deterministic. The model cannot change it.

- Validates every tool call: schema, risk level, target scope, budgets.
- Turns a model proposal into an approval request bound to (action, params, targets, state fingerprint, expiry).
- Signs approvals with HMAC. Rejects modified, expired, replayed or stale approvals before execution.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .audit import Audit
from .connectors.base import ACTIONS, TOOLS, RiskLevel


@dataclass
class Decision:
    allowed: bool
    code: str            # OK, INVALID_ARGS, UNKNOWN_TOOL, RISK_TOO_HIGH, OUT_OF_SCOPE, BUDGET_EXCEEDED, BLOCKED, ...
    reason: str
    parsed: Any = None


@dataclass
class Budget:
    max_tool_calls: int
    max_wall_seconds: float
    started: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    denied: int = 0


def _merge(base: dict, over: dict) -> None:
    """Deep-merge `over` into `base` (dicts merge, other values replace)."""
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class Policy:
    def __init__(self, policy_file: Path, audit: Audit, secret: str):
        self.cfg = yaml.safe_load(Path(policy_file).read_text())
        # Workspace-specific values (Slack channel and member IDs) live in a git-ignored local file.
        local = Path(policy_file).with_name(Path(policy_file).stem + ".local.yaml")
        if local.exists():
            _merge(self.cfg, yaml.safe_load(local.read_text()) or {})
        self.audit = audit
        if not secret:
            # Ephemeral secret: approvals do not survive a restart. Set NIGHTWATCH_APPROVAL_SECRET in production.
            secret = secrets.token_hex(32)
        self._secret = secret.encode()
        lim = self.cfg.get("limits", {})
        self.max_auto_risk = RiskLevel(lim.get("max_risk_without_approval", 1))
        self.block_at = RiskLevel(lim.get("block_risk_at_or_above", 3))
        self.ttl = float(lim.get("approval_ttl_seconds", 900))

    # ---------- scope ----------
    @staticmethod
    def _match(target: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(target, p) for p in patterns)

    def _in_scope(self, targets: list[str], allow_key: str) -> tuple[bool, str]:
        scope = self.cfg.get("scope", {})
        allow = scope.get(allow_key, [])
        deny = scope.get("deny_targets", [])
        for t in targets:
            if self._match(t, deny):
                return False, f"target {t} is explicitly denied"
            if not self._match(t, allow):
                return False, f"target {t} is outside the allowed scope"
        return True, ""

    def new_budget(self) -> Budget:
        lim = self.cfg.get("limits", {})
        return Budget(int(lim.get("max_tool_calls", 40)), float(lim.get("max_wall_seconds", 900)))

    # ---------- tool calls ----------
    def check_tool(self, name: str, args: dict, budget: Budget) -> Decision:
        tool = TOOLS.get(name)
        if tool is None:
            return Decision(False, "UNKNOWN_TOOL", f"no tool named {name}")
        if budget.tool_calls >= budget.max_tool_calls:
            return Decision(False, "BUDGET_EXCEEDED", f"tool-call budget of {budget.max_tool_calls} is used; write the report now")
        if time.monotonic() - budget.started > budget.max_wall_seconds:
            return Decision(False, "BUDGET_EXCEEDED", "time budget is used; write the report now")
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as e:
            return Decision(False, "INVALID_ARGS", e.errors(include_url=False).__repr__()[:800])
        if tool.risk > self.max_auto_risk:
            return Decision(False, "RISK_TOO_HIGH", f"tool risk {int(tool.risk)} needs approval")
        ok, why = self._in_scope(tool.targets(parsed), "read_targets")
        if not ok:
            return Decision(False, "OUT_OF_SCOPE", why)
        return Decision(True, "OK", "", parsed)

    # ---------- proposals and approvals ----------
    def _digest(self, rec: dict) -> str:
        core = {k: rec[k] for k in ("id", "incident_id", "action", "params", "targets", "fingerprint", "created_at", "expires_at")}
        return hashlib.sha256(_canon(core).encode()).hexdigest()

    def _sign(self, aid: str, digest: str, expires_at: float) -> str:
        return hmac.new(self._secret, f"{aid}|{digest}|{expires_at}".encode(), hashlib.sha256).hexdigest()

    def propose(self, incident_id: str | None, action_name: str, params: dict, justification: dict) -> Decision:
        act = ACTIONS.get(action_name)
        acfg = self.cfg.get("actions", {})
        if act is None:
            return Decision(False, "UNKNOWN_ACTION", f"no action named {action_name}; available: {sorted(ACTIONS)}")
        try:
            p = act.params_model.model_validate(params)
        except ValidationError as e:
            return Decision(False, "INVALID_ARGS", e.errors(include_url=False).__repr__()[:800])
        if act.risk >= self.block_at:
            self.audit.log("policy_blocked", {"action": action_name, "params": params, "risk": int(act.risk)}, incident_id)
            return Decision(False, "BLOCKED", f"risk level {int(act.risk)} actions are blocked in this version; recommend it to a human instead")
        if action_name not in acfg.get("allowed", []):
            return Decision(False, "NOT_ALLOWED", f"action {action_name} is not in the allowed action list")
        targets = act.targets(p)
        ok, why = self._in_scope(targets, "action_targets")
        if not ok:
            self.audit.log("policy_denied", {"action": action_name, "params": params, "reason": why}, incident_id)
            return Decision(False, "OUT_OF_SCOPE", why)
        cause = (justification or {}).get("root_cause_category")
        if cause in (self.cfg.get("fix_compatibility", {}) or {}).get(action_name, []):
            why = (f"{action_name} cannot remove a {cause}: it does not change configuration, environment, image or limits. "
                   "Use a rollback of the change if allowed, otherwise put the fix (and any restart after it) in the manual recommendations.")
            self.audit.log("policy_denied", {"action": action_name, "params": params, "cause": cause, "reason": why}, incident_id)
            return Decision(False, "INCOMPATIBLE_FIX", why)
        now = time.time()
        sev = (justification or {}).get("severity")
        ttl = float(((self.cfg.get("severity") or {}).get(sev) or {}).get("approval_ttl_seconds", self.ttl))
        rec = {"id": "apr-" + uuid.uuid4().hex[:8], "incident_id": incident_id, "action": action_name,
               "params": p.model_dump(mode="json"), "targets": targets, "fingerprint": act.fingerprint(p),
               "justification": justification, "created_at": now, "expires_at": now + ttl, "status": "pending"}
        rec["digest"] = self._digest(rec)
        self.audit.insert_approval(rec)
        self.audit.log("action_proposed", {k: rec[k] for k in ("id", "action", "params", "targets", "fingerprint", "justification", "expires_at", "digest")}, incident_id)
        rule = self._auto_rule(action_name, targets)
        if rule:
            self.approve(rec["id"], f"policy-rule:{rule}")
        return Decision(True, "PENDING_APPROVAL" if not rule else "AUTO_APPROVED", rec["id"], rec)

    def _auto_rule(self, action_name: str, targets: list[str]) -> str | None:
        for i, r in enumerate(self.cfg.get("auto_approve", []) or []):
            if not r.get("enabled") or r.get("action") != action_name:
                continue
            if not all(self._match(t, r.get("targets", [])) for t in targets):
                continue
            hour_ago = time.time() - 3600
            if any(self.audit.count_executed(action_name, t, hour_ago) >= int(r.get("max_per_hour", 1)) for t in targets):
                continue
            return r.get("name", f"rule-{i}")
        return None

    def approve(self, aid: str, approver: str) -> Decision:
        rec = self.audit.approval(aid)
        if rec is None:
            return Decision(False, "NOT_FOUND", f"no approval request {aid}")
        if rec["status"] != "pending":
            return Decision(False, "INVALID_STATE", f"request is {rec['status']}")
        if time.time() > rec["expires_at"]:
            self.audit.update_approval(aid, status="expired")
            return Decision(False, "STALE_APPROVAL", "request expired before approval")
        token = self._sign(aid, rec["digest"], rec["expires_at"])
        self.audit.update_approval(aid, status="approved", approver=approver, token=token, decided_at=time.time())
        self.audit.log("action_approved", {"id": aid, "approver": approver, "digest": rec["digest"]}, rec["incident_id"])
        return Decision(True, "APPROVED", aid)

    def reject(self, aid: str, approver: str, reason: str = "") -> Decision:
        rec = self.audit.approval(aid)
        if rec is None or rec["status"] != "pending":
            return Decision(False, "INVALID_STATE", "not pending")
        self.audit.update_approval(aid, status="rejected", approver=approver, decided_at=time.time())
        self.audit.log("action_rejected", {"id": aid, "approver": approver, "reason": reason}, rec["incident_id"])
        return Decision(True, "REJECTED", aid)

    def authorize(self, aid: str) -> Decision:
        """Final gate before execution. Recompute everything from stored data; trust nothing cached."""
        rec = self.audit.approval(aid)
        if rec is None:
            return Decision(False, "NOT_FOUND", f"no approval request {aid}")

        def deny(code: str, why: str) -> Decision:
            self.audit.log("execution_denied", {"id": aid, "code": code, "reason": why}, rec["incident_id"])
            return Decision(False, code, why)

        if rec["status"] == "executed" or rec.get("executed_at"):
            return deny("REPLAY", "this approval was already used")
        if rec["status"] != "approved" or not rec.get("token"):
            return deny("NOT_APPROVED", f"status is {rec['status']}")
        digest = self._digest(rec)
        if not hmac.compare_digest(digest, rec["digest"]):
            return deny("MODIFIED_APPROVAL", "the stored request does not match its digest (action, params, target or expiry changed)")
        if not hmac.compare_digest(self._sign(aid, digest, rec["expires_at"]), rec["token"]):
            return deny("BAD_SIGNATURE", "approval signature is not valid")
        if time.time() > rec["expires_at"]:
            self.audit.update_approval(aid, status="expired")
            return deny("STALE_APPROVAL", "approval expired")
        act = ACTIONS.get(rec["action"])
        if act is None or rec["action"] not in self.cfg.get("actions", {}).get("allowed", []) or act.risk >= self.block_at:
            return deny("NOT_ALLOWED", "action is no longer allowed by policy")
        p = act.params_model.model_validate(rec["params"])
        ok, why = self._in_scope(act.targets(p), "action_targets")
        if not ok:
            return deny("OUT_OF_SCOPE", why)
        current = act.fingerprint(p)
        if current != rec["fingerprint"]:
            self.audit.update_approval(aid, status="stale")
            return deny("STALE_STATE", f"target changed since approval: approved state '{rec['fingerprint']}', current '{current}'")
        return Decision(True, "AUTHORIZED", "", p)

    # ---------- people ----------
    def can_decide(self, user_id: str, severity: str | None, escalated: bool = False) -> bool:
        """True when this Slack user may approve, reject or instruct for an incident of this severity.
        After an escalation, the escalation contacts of that severity may decide too."""
        slack = self.cfg.get("slack") or {}
        approvers = list(((slack.get("approvers") or {}).get(severity or "P1")) or [])
        if escalated:
            approvers += ((slack.get("escalation") or {}).get("contacts") or {}).get(severity or "P1") or []
        return not approvers or user_id in approvers

    def escalation(self) -> dict:
        return (self.cfg.get("slack") or {}).get("escalation") or {}

    def severity_definitions(self) -> dict[str, str]:
        return {k: v.get("definition", "") for k, v in (self.cfg.get("severity") or {}).items()}
