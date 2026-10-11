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
    assert "Done:* Start container nw-redis" in texts and "✅ Resolved" in texts
    rejected = [r for r in audit.approvals() if r["action"] == "restart_container"][0]
    assert rejected["status"] == "rejected"
    assert audit.verify_chain()[0]


def test_ste_and_plan_message_limits():
    from nightwatch.notify.slack import render_text
    long = "This is a very long sentence; it has a semicolon " * 10 + ". Second. Third sentence."
    out = ste(long)
    assert ";" not in out and len(out.split()) <= 19 and out.endswith(("…", "."))
    rec = {"id": "apr-1", "action": "rollout_undo", "params": {"namespace": "shop", "deployment": "api", "to_revision": 4},
           "fingerprint": "x" * 100, "expires_at": time.time() + 60,
           "justification": {"severity": "P2", "expected_effect": "removes_root_cause", "reason": "r " * 50, "risk": "k " * 50,
                             "rollback": "b " * 50, "verification": [{"tool": "net_http_check", "args": {"url": "http://x/"}}] * 3}}
    msg = plan_message("inc-1", _report(sev="P2"), rec)
    text = render_text(msg)
    assert "Roll back deployment shop/api to revision 4." in text and "(+2 more)" in text
    assert msg["blocks"][0]["text"]["text"].startswith("🟡 P2")
    assert len(text.splitlines()) <= 12 and len(text.split()) <= 140, text   # short main message


def test_model_call_has_a_total_deadline(tmp_path):
    """A proxy that keeps a stream open with ping events must not block an incident forever."""
    from nightwatch.agent import Agent
    from nightwatch.policy import Policy as P

    class EndlessStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            while True:
                time.sleep(0.01)
                yield {"type": "ping"}

    class FakeMessages:
        def stream(self, **kw):
            return EndlessStream()

    s = Settings()
    s.neatlogs_api_key, s.llm_api_key = "", "test"
    audit = Audit(tmp_path / "a.db")
    agent = Agent(s, P(ROOT / "policy.yaml", audit, "x"), audit)
    agent.client = type("C", (), {"messages": FakeMessages()})()
    t = time.monotonic()
    assert agent._call_model([{"role": "user", "content": "x"}], "inc-1", deadline_s=0.2, attempts=2) is None
    assert time.monotonic() - t < 2
    assert sum(1 for e in audit.events() if e["kind"] == "llm_call_failed") == 2


def test_environment_binding(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from nightwatch.agent import Agent, RunStats
    from nightwatch.incident import IncidentRunner as R
    monkeypatch.setenv("NIGHTWATCH_AWS_PROFILE", "test")
    assert R.infer_environment("Users get 503 errors on http://localhost:8080.") == "lab"
    assert R.infer_environment("Staging services on AWS (ap-south-1) do not respond. Not the local lab.") == "aws"
    s = Settings()
    s.neatlogs_api_key, s.llm_api_key = "", "test"
    audit = Audit(tmp_path / "a.db")
    agent = Agent(s, Policy(ROOT / "policy.yaml", audit, "x"), audit)
    agent.set_scope(R.ENV_CONNECTORS["lab"])
    names = {t["name"] for t in agent.tools}
    assert not any(n.startswith("aws_") for n in names) and "docker_inspect" in names   # lab never sees company AWS
    block = SimpleNamespace(id="t1", name="aws_health_summary", input={})
    res = agent._dispatch("inc-1", block, agent.policy.new_budget(), RunStats(), [])
    assert res.get("is_error") and "OUT_OF_ENVIRONMENT" in res["content"]
    agent.set_scope(R.ENV_CONNECTORS["aws"])
    names = {t["name"] for t in agent.tools}
    assert "aws_health_summary" in names and "docker_inspect" not in names
