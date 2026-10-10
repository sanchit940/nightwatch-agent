"""Safety tests for the policy service and action worker. No model and no real infrastructure:
connector functions are replaced with fakes, so the tests prove the gate logic only."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nightwatch.audit import Audit
from nightwatch.connectors import ACTIONS, TOOLS
from nightwatch.connectors.base import ActionResult, ToolOutput
from nightwatch.policy import Policy
from nightwatch.worker import Worker

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = {"fp": "id1|running|t0|0|nw-back", "executed": []}
    act = ACTIONS["restart_container"]
    monkeypatch.setattr(act, "fingerprint", lambda p: state["fp"])
    monkeypatch.setattr(act, "execute", lambda p: state["executed"].append(p.container) or ActionResult(True, "restarted"))
    audit = Audit(tmp_path / "audit.db")
    policy = Policy(ROOT / "policy.yaml", audit, "test-secret")
    return policy, audit, Worker(policy, audit), state


def _propose(policy, container="nw-redis"):
    return policy.propose("inc-test", "restart_container", {"container": container},
                          {"reason": "test", "verification": []})


def test_read_tool_allowed_and_validated(env):
    policy, *_ = env
    b = policy.new_budget()
    assert policy.check_tool("docker_inspect", {"container": "nw-api"}, b).allowed
    d = policy.check_tool("docker_inspect", {"container": "--privileged"}, b)
    assert not d.allowed and d.code == "INVALID_ARGS"
    d = policy.check_tool("docker_inspect", {"container": "nw-api", "extra": 1}, b)
    assert not d.allowed and d.code == "INVALID_ARGS"


def test_unknown_tool_and_action_tool_rejected(env):
    policy, *_ = env
    b = policy.new_budget()
    assert policy.check_tool("restart_container", {"container": "nw-api"}, b).code == "UNKNOWN_TOOL"
    assert policy.check_tool("bash", {"cmd": "rm -rf /"}, b).code == "UNKNOWN_TOOL"


def test_out_of_scope_target_rejected(env):
    policy, *_ = env
    b = policy.new_budget()
    d = policy.check_tool("linux_check", {"target": "some-other-host", "check": "load"}, b)
    assert not d.allowed and d.code == "OUT_OF_SCOPE"
    d = _propose(policy, container="nightwatch-control-plane")
    assert not d.allowed and d.code == "OUT_OF_SCOPE"


def test_budget_enforced(env):
    policy, *_ = env
    b = policy.new_budget()
    b.tool_calls = b.max_tool_calls
    assert policy.check_tool("docker_stats", {}, b).code == "BUDGET_EXCEEDED"


def test_risk3_blocked(env):
    policy, *_ = env
    d = policy.propose("inc-test", "remove_container", {"container": "nw-redis"}, {"reason": "x"})
    assert not d.allowed and d.code == "BLOCKED"
    d = policy.propose("inc-test", "delete_namespace", {"namespace": "shop"}, {"reason": "x"})
    assert not d.allowed and d.code == "BLOCKED"


def test_unapproved_action_never_executes(env):
    policy, audit, worker, state = env
    d = _propose(policy)
    assert d.allowed and d.code == "PENDING_APPROVAL"
    r = worker.execute(d.reason)
    assert not r.executed and r.code == "NOT_APPROVED"
    assert state["executed"] == []


def test_approved_action_executes_once(env):
    policy, audit, worker, state = env
    aid = _propose(policy).reason
    assert policy.approve(aid, "human:test").allowed
    r = worker.execute(aid)
    assert r.executed and r.ok
    assert state["executed"] == ["nw-redis"]
    r2 = worker.execute(aid)
    assert not r2.executed and r2.code == "REPLAY"
    assert state["executed"] == ["nw-redis"]


def test_modified_approval_rejected(env):
    policy, audit, worker, state = env
    aid = _propose(policy).reason
    policy.approve(aid, "human:test")
    # Someone edits the approved request in storage to point at another container.
    audit.db.execute("UPDATE approvals SET params=? WHERE id=?", ('{"container":"nw-api"}', aid))
    r = worker.execute(aid)
    assert not r.executed and r.code == "MODIFIED_APPROVAL"
    assert state["executed"] == []


def test_forged_token_rejected(env):
    policy, audit, worker, state = env
    aid = _propose(policy).reason
    audit.update_approval(aid, status="approved", token="0" * 64)
    r = worker.execute(aid)
    assert not r.executed and r.code == "BAD_SIGNATURE"


def test_expired_approval_rejected(env):
    policy, audit, worker, state = env
    aid = _propose(policy).reason
    policy.approve(aid, "human:test")
    rec = audit.approval(aid)
    # Move expiry into the past AND re-sign consistently, to test expiry alone (not tampering).
    rec["expires_at"] = time.time() - 1
    digest = policy._digest(rec)
    audit.db.execute("UPDATE approvals SET expires_at=?, digest=?, token=? WHERE id=?",
                     (rec["expires_at"], digest, policy._sign(aid, digest, rec["expires_at"]), aid))
    r = worker.execute(aid)
    assert not r.executed and r.code == "STALE_APPROVAL"


def test_stale_state_rejected(env):
    policy, audit, worker, state = env
    aid = _propose(policy).reason
    policy.approve(aid, "human:test")
    state["fp"] = "id2|running|t1|1|nw-back"   # the container changed after approval
    r = worker.execute(aid)
    assert not r.executed and r.code == "STALE_STATE"
    assert state["executed"] == []


def test_audit_chain_detects_tampering(env):
    policy, audit, worker, state = env
    aid = _propose(policy).reason
    policy.approve(aid, "human:test")
    worker.execute(aid)
    assert audit.verify_chain()[0]
    audit.db.execute("UPDATE events SET payload='{}' WHERE seq=2")
    ok, seq = audit.verify_chain()
    assert not ok and seq == 2


def test_auto_approve_rule_is_bounded(env, monkeypatch):
    policy, audit, worker, state = env
    policy.cfg["auto_approve"] = [{"name": "r", "enabled": True, "action": "restart_container",
                                   "targets": ["docker:container/nw-redis"], "max_per_hour": 1}]
    d1 = _propose(policy)
    assert d1.code == "AUTO_APPROVED"
    worker.execute(d1.reason)
    state["fp"] = "id1|running|t2|0|nw-back"
    d2 = _propose(policy)
    assert d2.code == "PENDING_APPROVAL"       # rate limit reached: a human must decide
    d3 = _propose(policy, container="nw-api")
    assert d3.code == "PENDING_APPROVAL"       # target not covered by the rule


def test_secrets_redacted():
    from nightwatch.runner import redact
    s = redact("REDIS_PASSWORD=hunter2 API_KEY: abc123 Authorization: Bearer eyJhbGci.x.y url=redis://u:pw@host")
    assert "hunter2" not in s and "abc123" not in s and "eyJhbGci" not in s and ":pw@" not in s


def test_all_tools_are_read_or_diagnostic():
    assert all(int(t.risk) <= 1 for t in TOOLS.values())
    assert all(t.schema()["input_schema"].get("additionalProperties") is False for t in TOOLS.values())
