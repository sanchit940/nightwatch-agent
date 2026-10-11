"""Offline test of the Slack approval flow: scripted agent, dry-run Slack, fake action. No model, no Slack, no lab."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from nightwatch.agent import InvestigationResult, RunStats
from nightwatch.audit import Audit
from nightwatch.config import Settings
from nightwatch.connectors import ACTIONS
from nightwatch.connectors.base import ActionResult
from nightwatch.incident import IncidentRunner, make_approver
from nightwatch.notify.slack import SlackNotifier, plan_message, ste
from nightwatch.policy import Policy

ROOT = Path(__file__).resolve().parent.parent


def _report(resolution=None, sev="P0"):
    r = {"summary": "Redis is down. The API returns 503.", "severity": {"level": sev, "reason": "All requests fail."},
         "problem_statement": {"symptom": "503"}, "evidence": [{"tool": "docker_inspect", "finding": "nw-redis exited."}],
         "hypotheses": [], "root_cause": {"category": "dependency_down", "resource": "nw-redis", "description": "Redis stopped.",
                                          "confidence": "high"}, "known_unknowns": [], "recommendations": []}
    if resolution:
        r["resolution"] = resolution
    return r


def test_slack_guidance_then_approval_flow(tmp_path, monkeypatch):
    executed = []
    for name in ("restart_container", "start_container"):
        act = ACTIONS[name]
        monkeypatch.setattr(act, "fingerprint", lambda p: "fp-1")
        monkeypatch.setattr(act, "execute", lambda p, name=name: executed.append((name, p.container)) or ActionResult(True, "done"))
    s = Settings()
    s.neatlogs_api_key, s.llm_api_key = "", "test"
    audit = Audit(tmp_path / "a.db")
    policy = Policy(ROOT / "policy.yaml", audit, "secret")
    outbox = tmp_path / "outbox.jsonl"
    notifier = SlackNotifier(policy.cfg["slack"], audit, client=None, outbox=outbox)
    runner = IncidentRunner(s, audit, policy, make_approver("wait"), notifier=notifier, approval_wait_s=30, guidance_wait_s=5)

    rounds = []

    def fake_run(iid, message, messages=None, max_turns=30):
        rounds.append(message)
        just = {"severity": "P0", "expected_effect": "removes_root_cause", "reason": "r", "risk": "x", "rollback": "y", "verification": []}
        if len(rounds) == 1:
            d = policy.propose(iid, "restart_container", {"container": "nw-redis"}, just)
            return InvestigationResult(iid, _report(), [d.reason], RunStats(), [])
        if len(rounds) == 2:
            assert "APPROVER INSTRUCTION" in message and "start it, do not restart" in message
            d = policy.propose(iid, "start_container", {"container": "nw-redis"}, just)
            return InvestigationResult(iid, _report(), [d.reason], RunStats(), [])
        assert "Results of the approved actions" in message
        return InvestigationResult(iid, _report("resolved"), [], RunStats(), [])
    runner.agent.run = fake_run

    def human():
        # Plays the Slack approver: replace plan 1 with an instruction, then approve plan 2.
        for _ in range(100):
            inc = audit.incidents(1)
            pend = audit.approvals(status="pending")
            if inc and pend and pend[0]["action"] == "restart_container":
                audit.add_guidance(inc[0]["id"], "slack:U1", "start it, do not restart", pend[0]["id"])
            if inc and pend and pend[0]["action"] == "start_container":
                policy.approve(pend[0]["id"], "slack:U1")
                return
            time.sleep(0.1)
    t = threading.Thread(target=human)
    t.start()
    out = runner.handle("users get 503", max_rounds=5)
    t.join()

    assert executed == [("start_container", "nw-redis")]          # the replaced plan never ran
    assert out.status == "resolved" and out.rounds == 3
    msgs = [json.loads(l) for l in outbox.read_text().splitlines()]
    posts = [m for m in msgs if m["method"] == "chat.postMessage"]
    assert posts[0]["channel"] == policy.cfg["slack"]["channels"]["P0"] and posts[0]["thread_ts"] is None   # P0 channel, thread start
    assert all(m["thread_ts"] == posts[0]["ts"] for m in posts[1:])                    # everything else in the thread
    texts = "\n".join(m.get("text", "") for m in posts)
    assert "Executed:* Start container nw-redis" in texts and "Incident status: ✅ RESOLVED" in texts
    rejected = [r for r in audit.approvals() if r["action"] == "restart_container"][0]
    assert rejected["status"] == "rejected"
    assert audit.verify_chain()[0]


def test_ste_and_plan_message_limits():
    long = "This is a very long sentence " * 20 + ". Second. Third sentence."
    assert len(ste(long, 1, 25).split()) <= 26
    rec = {"id": "apr-1", "action": "rollout_undo", "params": {"namespace": "shop", "deployment": "api", "to_revision": 4},
           "fingerprint": "x" * 100, "expires_at": time.time() + 60, "justification": {"severity": "P2", "verification": []}}
    msg = plan_message("inc-1", _report(sev="P2"), rec, ["Step."] * 30)
    assert len(msg["blocks"]) <= 50
    assert all(len(b.get("text", {}).get("text", "")) <= 3000 for b in msg["blocks"] if b["type"] == "section")
    assert "Roll back deployment shop/api to revision 4." in json.dumps(msg)
    assert msg["blocks"][0]["text"]["text"].startswith("🟡 P2")


def test_expiry_reminder_and_escalation(tmp_path, monkeypatch):
    executed = []
    act = ACTIONS["start_container"]
    monkeypatch.setattr(act, "fingerprint", lambda p: "fp-1")
    monkeypatch.setattr(act, "execute", lambda p: executed.append(p.container) or ActionResult(True, "done"))
    s = Settings()
    s.neatlogs_api_key, s.llm_api_key = "", "test"
    audit = Audit(tmp_path / "a.db")
    policy = Policy(ROOT / "policy.yaml", audit, "secret")
    policy.cfg["severity"]["P0"]["approval_ttl_seconds"] = 3
    policy.cfg["slack"]["escalation"] = {"remind_at_fraction": 0.5, "max_levels": 1, "contacts": {"P0": ["UBOSS"]}}
    outbox = tmp_path / "outbox.jsonl"
    runner = IncidentRunner(s, audit, policy, make_approver("wait"), notifier=SlackNotifier(policy.cfg["slack"], audit, outbox=outbox),
                            approval_wait_s=30, guidance_wait_s=0)
    calls = []

    def fake_run(iid, message, messages=None, max_turns=30):
        calls.append(message)
        if len(calls) == 1:
            just = {"severity": "P0", "expected_effect": "removes_root_cause", "reason": "r", "risk": "x", "rollback": "y", "verification": []}
            return InvestigationResult(iid, _report(), [policy.propose(iid, "start_container", {"container": "nw-redis"}, just).reason], RunStats(), [])
        return InvestigationResult(iid, _report("resolved"), [], RunStats(), [])
    runner.agent.run = fake_run

    def boss():  # decides only on the escalated plan
        for _ in range(200):
            for r in audit.approvals(status="pending"):
                if (r["justification"] or {}).get("escalation_level") == 1:
                    assert policy.can_decide("UBOSS", "P0", escalated=True)
                    policy.approve(r["id"], "slack:UBOSS")
                    return
            time.sleep(0.05)
    t = threading.Thread(target=boss)
    t.start()
    out = runner.handle("users get 503", max_rounds=3)
    t.join()
    assert executed == ["nw-redis"] and out.status == "resolved"
    statuses = sorted(r["status"] for r in audit.approvals())
    assert statuses == ["executed", "expired"]
    text = outbox.read_text()
    assert "Reminder: plan" in text and "Escalation 1 of 1" in text and "<@UBOSS>" in text
    assert not policy.can_decide("URANDOM", "P0", escalated=True) or not policy.cfg["slack"]["approvers"]["P0"]
