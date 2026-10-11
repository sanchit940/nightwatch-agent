"""Self-hosted install: config folder, AWS role mode, SSM checks, cloud watch checks, starter policy. Offline."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from nightwatch import watch
from nightwatch.audit import Audit
from nightwatch.connectors import TOOLS, aws, linux
from nightwatch.incident import IncidentRunner
from nightwatch.policy import Policy

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / "deploy" / "config"


def call(name, **args):
    t = TOOLS[name]
    return t.fn(t.input_model.model_validate(args))


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    shutil.copytree(STARTER, d)
    monkeypatch.setenv("NIGHTWATCH_CONFIG_DIR", str(d))
    monkeypatch.setenv("NIGHTWATCH_DATA_DIR", str(tmp_path / "data"))
    return d


def test_config_folder_is_used_for_policy_inventory_watch(cfgdir, monkeypatch):
    from nightwatch.config import Settings
    from nightwatch.prompts import system_prompt
    (cfgdir / "inventory.yaml").write_text("environments: []\nssh_hosts: []\nnote: inventory-from-config-folder\n")
    s = Settings.from_env()
    assert s.policy_file == cfgdir / "policy.yaml" and s.audit_db.parent == cfgdir.parent / "data"
    assert "inventory-from-config-folder" in system_prompt("v6")
    monkeypatch.setenv("NIGHTWATCH_AWS_REGION", "eu-west-1")
    assert len(watch.load_checks()) == 2            # starter watch: alarms + load balancers in one region, no lab checks


def test_ssh_key_paths_are_relative_to_config_folder(cfgdir):
    (cfgdir / "inventory.yaml").write_text("ssh_hosts:\n  - {name: app-1, address: 10.0.1.15, user: nw, key_file: ssh/id, known_hosts: ssh/kh}\n")
    argv = linux._argv("host:app-1", "true")
    assert argv[argv.index("-i") + 1] == str(cfgdir / "ssh" / "id")


def test_role_mode_requires_the_account_pin(monkeypatch):
    monkeypatch.delenv("NIGHTWATCH_AWS_PROFILE", raising=False)
    monkeypatch.setenv("NIGHTWATCH_AWS_CREDENTIALS", "role")
    monkeypatch.delenv("NIGHTWATCH_AWS_ACCOUNT", raising=False)
    with pytest.raises(aws.AwsError) as e:
        aws._credentials()
    assert e.value.code == "NOT_CONFIGURED" and "pin" in str(e.value)
    monkeypatch.setenv("NIGHTWATCH_AWS_ACCOUNT", "123456789012")
    assert aws._credentials() is None                # None = default chain (the server's role)
    monkeypatch.setenv("NIGHTWATCH_AWS_CREDENTIALS", "keys")
    with pytest.raises(aws.AwsError):
        aws._credentials()


class FakeSSM:
    def __init__(self, fail=None):
        self.sent, self.fail, self.polls = [], fail, 0

    def send_command(self, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(kw)
        return {"Command": {"CommandId": "c-1"}}

    def get_command_invocation(self, CommandId, InstanceId):
        self.polls += 1
        if self.polls == 1:
            return {"Status": "InProgress"}
        return {"Status": "Success", "ResponseCode": 0,
                "StandardOutputContent": "MemAvailable: 100 kB\nowner ops@example.com\n", "StandardErrorContent": ""}


def test_ssm_runs_the_same_fixed_script_and_masks_output(monkeypatch):
    fake = FakeSSM()
    monkeypatch.setattr(aws, "_client", lambda service, region: fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = call("linux_check", target="ssm:i-0123456789abcdef0", check="memory")
    assert out.ok and "via=ssm" in out.text and "MemAvailable" in out.text
    assert "ops@example.com" not in out.text                     # masked like the AWS tools
    sent = fake.sent[0]
    assert sent["DocumentName"] == "AWS-RunShellScript" and sent["InstanceIds"] == ["i-0123456789abcdef0"]
    import shlex
    assert shlex.split(sent["Parameters"]["commands"][0]) == ["sh", "-c", linux.CHECKS["memory"][1], "_"]   # the constant script only


def test_ssm_errors_explain_the_fix(monkeypatch):
    class ClientError(Exception):
        response = {"Error": {"Code": "InvalidInstanceId"}}
    monkeypatch.setattr(aws, "_client", lambda service, region: FakeSSM(fail=ClientError("bad instance")))
    out = call("linux_check", target="ssm:i-0123456789abcdef0", check="load")
    assert not out.ok and out.error_code == "InvalidInstanceId" and "aws_ssm_instances" in out.text


def test_ssm_target_must_be_an_instance_id():
    with pytest.raises(Exception):
        TOOLS["linux_check"].input_model.model_validate({"target": "ssm:i-1; rm -rf /", "check": "load"})


class FakePager:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kw):
        return self.pages


class FakeAWS:
    def get_paginator(self, op):
        if op == "describe_alarms":
            return FakePager([{"MetricAlarms": [
                {"AlarmName": "api-5xx", "Namespace": "AWS/ApplicationELB", "MetricName": "HTTPCode_Target_5XX_Count", "StateReason": "Threshold crossed"},
                {"AlarmName": "staging-cpu", "StateReason": "x"}], "CompositeAlarms": []}])
        return FakePager([{"TargetGroups": [
            {"TargetGroupName": "web", "TargetGroupArn": "arn:web", "LoadBalancerArns": ["arn:lb"]},
            {"TargetGroupName": "stopped", "TargetGroupArn": "arn:stopped", "LoadBalancerArns": ["arn:lb"]},
            {"TargetGroupName": "healthy", "TargetGroupArn": "arn:ok", "LoadBalancerArns": ["arn:lb"]},
            {"TargetGroupName": "unattached", "TargetGroupArn": "arn:x", "LoadBalancerArns": []}]}])

    def describe_target_health(self, TargetGroupArn):
        th = {"arn:web": [{"TargetHealth": {"State": "unhealthy", "Reason": "Target.ResponseCodeMismatch"}}, {"TargetHealth": {"State": "healthy"}}],
              "arn:stopped": [{"TargetHealth": {"State": "unused", "Reason": "Target.InvalidState"}}],
              "arn:ok": [{"TargetHealth": {"State": "healthy"}}, {"TargetHealth": {"State": "unused", "Reason": "Target.NotInUse"}}]}
        return {"TargetHealthDescriptions": th[TargetGroupArn]}


def test_cloud_watch_checks_find_alarms_and_bad_targets(monkeypatch):
    monkeypatch.setattr(aws, "_client", lambda service, region: FakeAWS())
    alarms = watch.check_aws_alarms("eu-west-1", ignore=["staging-*"])
    assert [f.key for f in alarms] == ["aws-alarm:eu-west-1:api-5xx"] and "AWS CloudWatch alarm" in alarms[0].symptom
    tg = {f.key: f.symptom for f in watch.check_aws_targets("eu-west-1", ignore=[])}
    assert set(tg) == {"aws-targets:eu-west-1:web", "aws-targets:eu-west-1:stopped"}
    assert "1 of 2" in tg["aws-targets:eu-west-1:web"] and "Target.InvalidState" in tg["aws-targets:eu-west-1:stopped"]


def test_expensive_checks_run_once_per_interval():
    calls = []
    c = watch._every(3600, lambda: calls.append(1) or [watch.Finding("k", "s")])
    assert c() == c() and len(calls) == 1


def _runner(policy_file, tmp_path):
    policy = Policy(policy_file, Audit(tmp_path / "a.db"), "x")
    return SimpleNamespace(policy=policy, ENV_CONNECTORS=IncidentRunner.ENV_CONNECTORS, infer_environment=IncidentRunner.infer_environment)


def test_starter_environments_and_default(tmp_path):
    r = _runner(STARTER / "policy.yaml", tmp_path)
    env, conn = IncidentRunner.environment_for(r, "AWS CloudWatch alarm api-5xx in eu-west-1 is in ALARM")
    assert env == "aws" and {"aws", "linux", "network"} <= conn and "docker" not in conn
    env, conn = IncidentRunner.environment_for(r, "Health check of website failed: GET https://shop.example/ -> 502")
    assert env == "aws"                                   # default_environment: a cloud install has no lab
    assert IncidentRunner.environment_for(r, "Jenkins build fails")[0] == "ci"
    assert IncidentRunner.environment_for(r, "x", explicit="unknown-env")[1] == {"knowledge"}   # never every tool


def test_repo_policy_keeps_lab_binding(tmp_path):
    r = _runner(ROOT / "policy.yaml", tmp_path)
    env, conn = IncidentRunner.environment_for(r, "Users get 503 errors on http://localhost:8080")
    assert env == "lab" and "docker" in conn
    assert IncidentRunner.environment_for(r, "Staging on AWS (ap-south-1) is down")[1] == {"aws", "knowledge"}


def test_starter_policy_is_read_only(tmp_path):
    policy = Policy(STARTER / "policy.yaml", Audit(tmp_path / "a.db"), "x")
    d = policy.propose("inc-1", "ci_rerun_failed", {"provider": "github", "project": "org/repo", "run_id": "1"},
                       {"reason": "flaky", "verification": [], "root_cause_category": "flaky_failure"})
    assert not d.allowed
    b = policy.new_budget()
    assert policy.check_tool("aws_ssm_instances", {}, b).allowed
    assert policy.check_tool("linux_check", {"target": "ssm:i-0123456789abcdef0", "check": "load"}, b).allowed
    assert not policy.check_tool("docker_inspect", {"container": "nw-api"}, b).allowed
