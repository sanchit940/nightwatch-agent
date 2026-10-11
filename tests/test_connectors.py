"""Offline tests for the Grafana/Loki, neatlogs, AWS and SSH connectors (no network, no cloud account)."""

from __future__ import annotations

import datetime as dt

import pytest

from nightwatch.connectors import TOOLS, aws, grafana, linux, neatlogs_api


def call(name, **args):
    t = TOOLS[name]
    return t.fn(t.input_model.model_validate(args))


def test_aws_requires_named_profile(monkeypatch):
    monkeypatch.delenv("NIGHTWATCH_AWS_PROFILE", raising=False)
    out = call("aws_ec2_instances")
    assert not out.ok and out.error_code == "NOT_CONFIGURED"


def test_aws_ec2_and_vpc_with_stubbed_api(monkeypatch):
    import boto3
    from botocore.stub import Stubber
    ec2 = boto3.client("ec2", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    st = Stubber(ec2)
    st.add_response("describe_instances", {"Reservations": [{"Instances": [{
        "InstanceId": "i-0123456789abcdef0", "InstanceType": "t3.small", "State": {"Name": "running"},
        "Placement": {"AvailabilityZone": "us-east-1a"}, "PrivateIpAddress": "10.0.1.5", "SubnetId": "subnet-0aaa1111",
        "VpcId": "vpc-0bbb2222", "SecurityGroups": [{"GroupId": "sg-0ccc3333", "GroupName": "web"}],
        "LaunchTime": dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc), "Tags": [{"Key": "Name", "Value": "shop-web-1"}]}]}]})
    st.add_response("describe_instance_status", {"InstanceStatuses": [{"InstanceId": "i-0123456789abcdef0",
        "SystemStatus": {"Status": "ok"}, "InstanceStatus": {"Status": "impaired"}}]})
    st.add_response("describe_subnets", {"Subnets": [{"SubnetId": "subnet-0aaa1111", "VpcId": "vpc-0bbb2222", "CidrBlock": "10.0.1.0/24",
                                                      "AvailabilityZone": "us-east-1a", "AvailableIpAddressCount": 250}]})
    st.add_response("describe_route_tables", {"RouteTables": [{"RouteTableId": "rtb-0ddd4444", "Associations": [],
        "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0eee5555", "State": "blackhole"}]}]})
    st.add_response("describe_network_acls", {"NetworkAcls": [{"NetworkAclId": "acl-0fff6666", "IsDefault": False, "Entries": [
        {"RuleNumber": 100, "Protocol": "6", "RuleAction": "deny", "Egress": False, "CidrBlock": "0.0.0.0/0", "PortRange": {"From": 443, "To": 443}}]}]})
    st.add_response("describe_nat_gateways", {"NatGateways": [{"NatGatewayId": "nat-0eee5555", "State": "deleted", "SubnetId": "subnet-0aaa1111"}]})
    st.add_response("describe_internet_gateways", {"InternetGateways": []})
    st.activate()
    monkeypatch.setattr(aws, "_client", lambda service, region: ec2)
    out = call("aws_ec2_instances")
    assert out.ok and "shop-web-1" in out.text and '"instance":"impaired"' in out.text
    out = call("aws_vpc_network", subnet_id="subnet-0aaa1111")
    assert out.ok and "(blackhole)" in out.text and "in #100 deny 6 443-443" in out.text and '"state":"deleted"' in out.text


def test_aws_rejects_bad_ids():
    with pytest.raises(Exception):
        TOOLS["aws_security_groups"].input_model.model_validate({"group_ids": ["sg-1; rm -rf /"]})


def test_loki_streams_and_matrix(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "http://loki.invalid")
    streams = {"data": {"resultType": "streams", "result": [{"stream": {"container": "nw-api"},
               "values": [["1791700000000000000", "ERROR redis dns lookup failed"], ["1791700001000000000", "INFO ok"]]}]}}
    monkeypatch.setattr(grafana, "_get", lambda *a, **k: (True, streams, None))
    out = call("loki_query", logql='{container="nw-api"}')
    assert out.ok and "nw-api\tERROR redis" in out.text
    matrix = {"data": {"resultType": "matrix", "result": [{"metric": {"container": "nw-api"},
              "values": [[1791700000, "0"], [1791700060, "37"]]}]}}
    monkeypatch.setattr(grafana, "_get", lambda *a, **k: (True, matrix, None))
    out = call("loki_query", logql='sum by (container) (count_over_time({container=~".+"} |= "ERROR" [1m]))')
    assert out.ok and "max=37" in out.text and "first_nonzero=" in out.text


def test_neatlogs_needs_read_credentials_and_formats(monkeypatch):
    monkeypatch.delenv("NEATLOGS_READ_TOKEN", raising=False)
    monkeypatch.delenv("NEATLOGS_PUBLIC_API_KEY", raising=False)
    assert call("neatlogs_traces").error_code == "NOT_CONFIGURED"
    monkeypatch.setattr(neatlogs_api, "_call", lambda *a, **k: (True, {"traces": [
        {"traceId": "t-abc123", "status": "error", "durationMs": 912.0, "toolCallsCount": 3, "totalCostUsd": 0.01, "secret": None}]}, None))
    out = call("neatlogs_traces", status="error")
    assert out.ok and '"traceId": "t-abc123"' in out.text and '"status": "error"' in out.text


def test_ssh_argv_is_strict_and_quoted():
    argv = linux._argv("host:nw-vm", 'echo "$1"', "shop-worker.service")
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and "StrictHostKeyChecking=yes" in argv
    assert argv[-1].startswith("sh -c ") and "shop-worker.service" in argv[-1]
    with pytest.raises(Exception):
        TOOLS["linux_service_status"].input_model.model_validate({"target": "host:nw-vm", "unit": "x.service; reboot"})


def test_unconfigured_connectors_are_hidden(monkeypatch):
    from nightwatch.connectors import enabled_tools
    for v in ("NIGHTWATCH_AWS_PROFILE", "NEATLOGS_READ_TOKEN", "NEATLOGS_PUBLIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    names = enabled_tools()
    assert not any(n.startswith(("aws_", "neatlogs_")) for n in names) and "docker_inspect" in names
