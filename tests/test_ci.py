"""Offline tests for the CI/CD connector (Jenkins, GitHub Actions, GitLab CI) with recorded API shapes."""

from __future__ import annotations

from pathlib import Path

from nightwatch.audit import Audit
from nightwatch.connectors import TOOLS, ci
from nightwatch.policy import Policy

ROOT = Path(__file__).resolve().parent.parent


def call(name, **args):
    t = TOOLS[name]
    return t.fn(t.input_model.model_validate(args))


def test_github_runs_marks_failing_since_and_last_good(monkeypatch):
    runs = {"workflow_runs": [
        {"id": 3, "name": "tests", "conclusion": "failure", "status": "completed", "head_branch": "main", "head_sha": "c" * 40,
         "event": "push", "run_started_at": "2026-10-11T10:00:00Z", "updated_at": "2026-10-11T10:02:00Z", "head_commit": {"message": "bump cache", "author": {"name": "dev"}}},
        {"id": 2, "name": "tests", "conclusion": "failure", "status": "completed", "head_branch": "main", "head_sha": "b" * 40,
         "event": "push", "run_started_at": "2026-10-11T09:00:00Z", "updated_at": "2026-10-11T09:02:00Z", "head_commit": {"message": "x", "author": {"name": "dev"}}},
        {"id": 1, "name": "tests", "conclusion": "success", "status": "completed", "head_branch": "main", "head_sha": "a" * 40,
         "event": "push", "run_started_at": "2026-10-11T08:00:00Z", "updated_at": "2026-10-11T08:01:30Z", "head_commit": {"message": "ok", "author": {"name": "dev"}}}]}
    monkeypatch.setattr(ci, "_http", lambda *a, **k: runs)
    out = call("ci_runs", provider="github", project="o/r")
    assert out.ok and "FAILING: last 2 finished run(s) failed. First failure: 2" in out.text and "Last good: 1" in out.text
    assert '"duration_s": 90' in out.text


def test_gitlab_failure_logs_errors_first_and_redacted(monkeypatch):
    log = "\n".join(["$ make test"] + [f"ok line {i}" for i in range(200)] +
                    ["API_KEY=sk-live-abcdefghijklmnopqrstu", "ERROR: test_checkout_total FAILED: expected 105.00, got 100.00", "exit code 1"])

    def fake(method, url, headers=None, body=None, timeout=20, text=False, follow_without_auth=False):
        if url.endswith("/trace"):
            return log
        return [{"id": 7, "stage": "test", "name": "unit", "failure_reason": "script_failure", "allow_failure": False}]
    monkeypatch.setattr(ci, "_http", fake)
    out = call("ci_failure_logs", provider="gitlab", project="group/app", run_id="42")
    assert out.ok and out.text.index("test_checkout_total FAILED") < out.text.index("== last")
    assert "sk-live-abcdefghijklmnopqrstu" not in out.text


def test_jenkins_compare_shows_parameter_change_and_commits(monkeypatch):
    monkeypatch.setenv("JENKINS_URL", "http://jenkins.invalid")
    builds = {"builds": [
        {"number": 5, "result": "FAILURE", "actions": [{"parameters": [{"name": "FAULT", "value": "failing-test"}]}],
         "changeSets": [{"items": [{"msg": "change price rounding", "commitId": "f00ba4", "author": {"fullName": "dev"}, "affectedPaths": ["shop/price.py"]}]}]},
        {"number": 4, "result": "SUCCESS", "actions": [{"parameters": [{"name": "FAULT", "value": "none"}]}], "changeSets": []}]}

    def fake(method, url, headers=None, body=None, timeout=20, text=False, follow_without_auth=False):
        if "/5/api/json" in url:
            return {"number": 5, "result": "FAILURE", "actions": [{"parameters": [{"name": "FAULT", "value": "failing-test"}]}]}
        if "/4/api/json" in url:
            return {"number": 4, "result": "SUCCESS", "actions": [{"parameters": [{"name": "FAULT", "value": "none"}]}]}
        return builds
    monkeypatch.setattr(ci, "_http", fake)
    out = call("ci_compare_runs", provider="jenkins", project="nightwatch-lab-build", good_run_id="4", bad_run_id="5")
    assert out.ok and '"FAULT"' in out.text and "failing-test" in out.text and "shop/price.py" in out.text


def test_same_commit_points_away_from_code(monkeypatch):
    monkeypatch.setattr(ci.GitHub, "run", lambda self, p, rid: {"id": rid, "status": "x", "commit_full": "a" * 40, "params": {}})
    out = call("ci_compare_runs", provider="github", project="o/r", good_run_id="1", bad_run_id="2")
    assert out.ok and "code did not change" in out.text


def test_jenkins_needs_url(monkeypatch):
    monkeypatch.delenv("JENKINS_URL", raising=False)
    assert call("ci_runs", provider="jenkins", project="job").error_code == "NOT_CONFIGURED"


def test_rerun_policy(tmp_path, monkeypatch):
    from nightwatch.connectors import ACTIONS
    monkeypatch.setattr(ACTIONS["ci_rerun_failed"], "fingerprint", lambda p: "failure|t1|1")
    audit = Audit(tmp_path / "a.db")
    policy = Policy(ROOT / "policy.yaml", audit, "x")
    params = {"provider": "github", "project": "sanchit940/nightwatch-agent", "run_id": "123"}
    d = policy.propose("i", "ci_rerun_failed", params, {"root_cause_category": "test_failure"})
    assert not d.allowed and d.code == "INCOMPATIBLE_FIX"            # a re-run cannot fix a real test failure
    d = policy.propose("i", "ci_rerun_failed", params, {"root_cause_category": "flaky_failure"})
    assert d.allowed                                                   # a flaky failure may be re-run (after approval)
    d = policy.propose("i", "ci_rerun_failed", {**params, "project": "someone/else"}, {"root_cause_category": "flaky_failure"})
    assert not d.allowed and d.code == "OUT_OF_SCOPE"


def _server(handler_fn):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            handler_fn(self)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_http_never_sends_token_to_sso_host_and_says_why():
    seen = []

    def h(req):
        seen.append((req.headers.get("Host"), req.path, req.headers.get("Authorization")))
        if req.path == "/api/json":                      # same host: follow with the token
            req.send_response(302); req.send_header("Location", "/api/json/"); req.end_headers()
        elif req.path == "/api/json/":                   # login proxy: other host (localhost vs 127.0.0.1)
            req.send_response(302); req.send_header("Location", f"http://localhost:{req.server.server_port}/okta/login"); req.end_headers()
        else:
            req.send_response(200); req.send_header("Content-Type", "text/html"); req.end_headers(); req.wfile.write(b"<html>login</html>")
    srv = _server(h)
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        try:
            ci._http("GET", base + "/api/json", {"Authorization": "Basic c2VjcmV0"})
            raise AssertionError("expected SSO_REDIRECT")
        except ci.CIError as e:
            assert e.code == "SSO_REDIRECT" and "not sent" in str(e)
        assert [s[1] for s in seen] == ["/api/json", "/api/json/"] and all(s[2] for s in seen)   # okta host never called
        try:
            ci._http("GET", f"http://localhost:{srv.server_port}/okta/login")
            raise AssertionError("expected SSO_LOGIN_PAGE")
        except ci.CIError as e:
            assert e.code == "SSO_LOGIN_PAGE"
    finally:
        srv.shutdown()


def test_jenkins_token_without_user_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("JENKINS_URL", "http://jenkins.invalid")
    monkeypatch.delenv("JENKINS_USER", raising=False)
    monkeypatch.delenv("JENKINS_TOKEN", raising=False)
    monkeypatch.setenv("JENKINS_API_TOKEN", "x")
    out = call("ci_runs", provider="jenkins", project="app/main")
    assert not out.ok and "JENKINS_USER" in out.text
