"""AWS connector (read-only): EC2, EBS, security groups, VPC routing (routes, NACLs, NAT), load balancer target
health, CloudWatch metrics/alarms/logs, CloudTrail change history, EKS, IAM policy simulation, Route 53.

Safety:
- Uses ONLY the named profile in NIGHTWATCH_AWS_PROFILE (never the default credential chain).
- If NIGHTWATCH_AWS_ACCOUNT is set, every session confirms the account ID first and refuses any other account.
- Every call is a Describe/List/Get/Lookup/Simulate call. The IAM user should have a read-only policy
  (see docs/aws-readonly-policy.json). There are no AWS change actions in v1.
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from typing import Annotated

from pydantic import Field

from ..runner import redact
from .base import Args, RiskLevel, ToolOutput, tool

REGION = Annotated[str | None, Field(pattern=r"^[a-z]{2}(-gov)?-[a-z]+-\d$", description="AWS region; default NIGHTWATCH_AWS_REGION")]
AWS_ID = r"^[a-z]{1,8}-[0-9a-f]{8,17}$"


class AwsError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


@lru_cache(maxsize=8)
def _session(profile: str, region: str):
    import boto3
    s = boto3.Session(profile_name=profile, region_name=region)
    want = os.environ.get("NIGHTWATCH_AWS_ACCOUNT")
    if want:
        got = s.client("sts").get_caller_identity()["Account"]
        if got != want:
            raise AwsError("WRONG_ACCOUNT", f"profile {profile} is account {got}, expected {want}")
    return s


def _client(service: str, region: str | None):
    from botocore.config import Config
    profile = os.environ.get("NIGHTWATCH_AWS_PROFILE")
    if not profile:
        raise AwsError("NOT_CONFIGURED", "set NIGHTWATCH_AWS_PROFILE to a read-only profile (the default profile is never used)")
    region = region or os.environ.get("NIGHTWATCH_AWS_REGION") or "us-east-1"
    cfg = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2, "mode": "standard"},
                 user_agent_extra="nightwatch/0.1")
    return _session(profile, region).client(service, config=cfg)


def _run(fn) -> ToolOutput:
    """Common error handling and output limits for all AWS tools."""
    try:
        text = fn()
        return ToolOutput(True, redact(text)[:14000] or "(no results)")
    except AwsError as e:
        return ToolOutput(False, str(e), e.code)
    except Exception as e:  # noqa: BLE001 - botocore errors carry a code we pass to the model
        code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
        return ToolOutput(False, f"{type(e).__name__}: {str(e)[:400]}", code or "AWS_ERROR")


def _name(tags: list | None) -> str | None:
    return next((t["Value"] for t in tags or [] if t.get("Key") == "Name"), None)


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(dt, "strftime") else dt


def _j(obj) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


# ---------- EC2 / EBS ----------

class Ec2Args(Args):
    region: REGION = None
    instance_ids: list[Annotated[str, Field(pattern=AWS_ID)]] = Field(default_factory=list, max_length=20)
    name_contains: str | None = Field(None, max_length=60, description="Filter by Name tag substring")
    state: str | None = Field(None, pattern=r"^(pending|running|stopping|stopped|shutting-down|terminated)$")
    limit: int = Field(25, ge=1, le=100)


@tool("aws_ec2_instances", "EC2 instances with state, type, AZ, IPs, subnet, VPC, security groups, launch time, and status checks (system/instance reachability, scheduled events).",
      Ec2Args, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/ec2"])
def aws_ec2_instances(a: Ec2Args) -> ToolOutput:
    def go():
        ec2 = _client("ec2", a.region)
        kw: dict = {"MaxResults": 1000} if not a.instance_ids else {"InstanceIds": a.instance_ids}
        filters = []
        if a.state:
            filters.append({"Name": "instance-state-name", "Values": [a.state]})
        if a.name_contains:
            filters.append({"Name": "tag:Name", "Values": [f"*{a.name_contains}*"]})
        if filters:
            kw["Filters"] = filters
        insts = [i for r in ec2.describe_instances(**kw)["Reservations"] for i in r["Instances"]][: a.limit]
        ids = [i["InstanceId"] for i in insts]
        status = {}
        if ids:
            for st in ec2.describe_instance_status(InstanceIds=ids, IncludeAllInstances=True)["InstanceStatuses"]:
                status[st["InstanceId"]] = {"system": st.get("SystemStatus", {}).get("Status"), "instance": st.get("InstanceStatus", {}).get("Status"),
                                            "events": [e.get("Code") for e in st.get("Events", [])] or None}
        rows = [_j({"id": i["InstanceId"], "name": _name(i.get("Tags")), "state": i["State"]["Name"], "type": i["InstanceType"],
                    "az": i.get("Placement", {}).get("AvailabilityZone"), "private_ip": i.get("PrivateIpAddress"),
                    "public_ip": i.get("PublicIpAddress"), "subnet": i.get("SubnetId"), "vpc": i.get("VpcId"),
                    "security_groups": [g["GroupId"] for g in i.get("SecurityGroups", [])], "launched": _iso(i.get("LaunchTime")),
                    "state_reason": (i.get("StateReason") or {}).get("Message"), "status_checks": status.get(i["InstanceId"])})
                for i in insts]
        return "\n".join(rows)
    return _run(go)


class EbsArgs(Args):
    region: REGION = None
    instance_id: str | None = Field(None, pattern=AWS_ID)
    volume_ids: list[Annotated[str, Field(pattern=AWS_ID)]] = Field(default_factory=list, max_length=20)


@tool("aws_ebs_volumes", "EBS volumes (by instance or IDs): state, size, type, IOPS/throughput, attachment state, and volume status checks (io-enabled, io-performance).",
      EbsArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/ebs"])
def aws_ebs_volumes(a: EbsArgs) -> ToolOutput:
    def go():
        ec2 = _client("ec2", a.region)
        kw: dict = {}
        if a.volume_ids:
            kw["VolumeIds"] = a.volume_ids
        if a.instance_id:
            kw["Filters"] = [{"Name": "attachment.instance-id", "Values": [a.instance_id]}]
        allv = [v for page in ec2.get_paginator("describe_volumes").paginate(**kw) for v in page["Volumes"]]
        vols = allv[:50]
        st = {}
        if vols:
            for s in ec2.describe_volume_status(VolumeIds=[v["VolumeId"] for v in vols])["VolumeStatuses"]:
                st[s["VolumeId"]] = {d["Name"]: d["Status"] for d in s.get("VolumeStatus", {}).get("Details", [])}
        return "\n".join(_j({"id": v["VolumeId"], "state": v["State"], "size_gib": v["Size"], "type": v["VolumeType"], "iops": v.get("Iops"),
                             "throughput": v.get("Throughput"), "attachments": [(x["InstanceId"], x["Device"], x["State"]) for x in v.get("Attachments", [])],
                             "status": st.get(v["VolumeId"])}) for v in vols) + (f"\n[showing 50 of {len(allv)} volumes]" if len(allv) > 50 else "")
    return _run(go)


# ---------- network ----------

class SgArgs(Args):
    region: REGION = None
    group_ids: list[Annotated[str, Field(pattern=AWS_ID)]] = Field(min_length=1, max_length=10)


@tool("aws_security_groups", "Security group rules (inbound and outbound): protocol, ports, sources (CIDR, prefix list, other security group).",
      SgArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/sg"])
def aws_security_groups(a: SgArgs) -> ToolOutput:
    def rule(p):
        ports = "all" if p.get("IpProtocol") == "-1" else f"{p.get('FromPort')}-{p.get('ToPort')}"
        src = [r["CidrIp"] for r in p.get("IpRanges", [])] + [r["CidrIpv6"] for r in p.get("Ipv6Ranges", [])] + \
              [r["GroupId"] for r in p.get("UserIdGroupPairs", [])] + [r["PrefixListId"] for r in p.get("PrefixListIds", [])]
        return f"{p.get('IpProtocol')}:{ports} <- {','.join(src) or '-'}"

    def go():
        gs = _client("ec2", a.region).describe_security_groups(GroupIds=a.group_ids)["SecurityGroups"]
        return "\n".join(_j({"id": g["GroupId"], "name": g.get("GroupName"), "vpc": g.get("VpcId"),
                             "inbound": [rule(p) for p in g.get("IpPermissions", [])],
                             "outbound": [rule(p).replace("<-", "->") for p in g.get("IpPermissionsEgress", [])]}) for g in gs)
    return _run(go)


class VpcArgs(Args):
    region: REGION = None
    subnet_id: str | None = Field(None, pattern=AWS_ID, description="Give a subnet to see its effective route table and NACL")
    vpc_id: str | None = Field(None, pattern=AWS_ID)


@tool("aws_vpc_network", "VPC path diagnostics: subnets, the effective route table of a subnet (routes, blackhole routes), network ACL entries, NAT gateways and internet gateways.",
      VpcArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/vpc"])
def aws_vpc_network(a: VpcArgs) -> ToolOutput:
    def go():
        if not (a.subnet_id or a.vpc_id):
            raise AwsError("INVALID_ARGS", "give subnet_id or vpc_id")
        ec2 = _client("ec2", a.region)
        out = []
        vpc = a.vpc_id
        if a.subnet_id:
            sn = ec2.describe_subnets(SubnetIds=[a.subnet_id])["Subnets"][0]
            vpc = sn["VpcId"]
            out.append(_j({"subnet": sn["SubnetId"], "cidr": sn["CidrBlock"], "az": sn["AvailabilityZone"], "free_ips": sn["AvailableIpAddressCount"],
                           "public_ip_on_launch": sn.get("MapPublicIpOnLaunch")}))
            rts = ec2.describe_route_tables(Filters=[{"Name": "association.subnet-id", "Values": [a.subnet_id]}])["RouteTables"] or \
                  ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "association.main", "Values": ["true"]}])["RouteTables"]
            nacls = ec2.describe_network_acls(Filters=[{"Name": "association.subnet-id", "Values": [a.subnet_id]}])["NetworkAcls"]
        else:
            rts = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["RouteTables"]
            nacls = ec2.describe_network_acls(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["NetworkAcls"]
        for rt in rts[:10]:
            routes = [f"{r.get('DestinationCidrBlock') or r.get('DestinationPrefixListId') or r.get('DestinationIpv6CidrBlock')} -> "
                      f"{r.get('GatewayId') or r.get('NatGatewayId') or r.get('TransitGatewayId') or r.get('NetworkInterfaceId') or r.get('VpcPeeringConnectionId') or r.get('InstanceId')}"
                      f" ({r.get('State')})" for r in rt.get("Routes", [])]
            out.append(_j({"route_table": rt["RouteTableId"], "main": any(x.get("Main") for x in rt.get("Associations", [])), "routes": routes}))
        for n in nacls[:10]:
            ent = [f"{'out' if e['Egress'] else 'in'} #{e['RuleNumber']} {e['RuleAction']} {e.get('Protocol')} "
                   f"{(e.get('PortRange') or {}).get('From', 'all')}-{(e.get('PortRange') or {}).get('To', '')} {e.get('CidrBlock') or e.get('Ipv6CidrBlock')}"
                   for e in sorted(n.get("Entries", []), key=lambda e: (e["Egress"], e["RuleNumber"]))]
            out.append(_j({"network_acl": n["NetworkAclId"], "default": n.get("IsDefault"), "entries": ent}))
        nats = ec2.describe_nat_gateways(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["NatGateways"]
        out += [_j({"nat_gateway": g["NatGatewayId"], "state": g["State"], "subnet": g.get("SubnetId"), "failure": g.get("FailureMessage")}) for g in nats[:10]]
        igws = ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc]}])["InternetGateways"]
        out += [_j({"internet_gateway": g["InternetGatewayId"], "attachments": [x.get("State") for x in g.get("Attachments", [])]}) for g in igws]
        return "\n".join(out)
    return _run(go)


class ElbArgs(Args):
    region: REGION = None
    name: str | None = Field(None, pattern=r"^[A-Za-z0-9-]{1,32}$", description="Load balancer name; omit for all")
    problems_only: bool = Field(True, description="List only target groups with non-healthy or no targets; count the healthy ones")


@tool("aws_elb_health", "Load balancers: state, scheme, listeners, and target health per target group (unhealthy, unused, empty) with reason and health check. Lists problems first; says how many healthy groups are not listed.",
      ElbArgs, RiskLevel.READ, "aws", timeout_s=60, targets=lambda a: [f"aws:{a.region or 'default'}/elb"])
def aws_elb_health(a: ElbArgs) -> ToolOutput:
    def go():
        elb = _client("elbv2", a.region)
        lbs = [lb for page in elb.get_paginator("describe_load_balancers").paginate(**({"Names": [a.name]} if a.name else {}))
               for lb in page["LoadBalancers"]]
        out, hidden = [], 0
        for lb in lbs:
            arn = lb["LoadBalancerArn"]
            listeners = [f"{l['Protocol']}:{l['Port']}" for l in elb.describe_listeners(LoadBalancerArn=arn)["Listeners"]]
            tgs, ok_groups = [], 0
            for tg in [t for p in elb.get_paginator("describe_target_groups").paginate(LoadBalancerArn=arn) for t in p["TargetGroups"]]:
                th = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
                healthy = th and all(t["TargetHealth"]["State"] == "healthy" for t in th)
                if a.problems_only and healthy:
                    ok_groups += 1
                    continue
                tgs.append({"target_group": tg["TargetGroupName"], "port": tg.get("Port"),
                            "health_check": f"{tg.get('HealthCheckProtocol')}:{tg.get('HealthCheckPort')}{tg.get('HealthCheckPath') or ''}",
                            "targets": [f"{t['Target']['Id']}:{t['Target'].get('Port')} {t['TargetHealth']['State']} {t['TargetHealth'].get('Reason') or ''}".strip()
                                        for t in th] or ["(no targets registered)"]})
            hidden += ok_groups
            out.append(_j({"lb": lb["LoadBalancerName"], "type": lb["Type"], "scheme": lb["Scheme"], "state": lb["State"]["Code"],
                           "dns": lb["DNSName"], "listeners": listeners, "healthy_target_groups_not_listed": ok_groups, "problem_target_groups": tgs}))
        text = "\n".join(out)
        if len(text) > 12000:
            text = text[:12000] + f"\n[output cut at 12000 characters: {len(lbs)} load balancers in total; ask for one by name]"
        return text + f"\n(load balancers: {len(lbs)}; healthy target groups not listed: {hidden})"
    return _run(go)


# ---------- CloudWatch / CloudTrail ----------

class MetricArgs(Args):
    region: REGION = None
    namespace: str = Field(pattern=r"^[A-Za-z0-9/._:-]{2,100}$", description="For example AWS/EC2, AWS/ApplicationELB, AWS/RDS")
    metric: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    dimensions: dict[str, str] = Field(default_factory=dict, description='For example {"InstanceId": "i-0abc..."}')
    stat: str = Field("Average", pattern=r"^(Average|Sum|Maximum|Minimum|SampleCount|p50|p90|p95|p99)$")
    minutes: int = Field(60, ge=5, le=4320)
    period_s: int = Field(60, ge=60, le=3600)


@tool("aws_cloudwatch_metric", "One CloudWatch metric over time, summarized: first, min, max, last and the time of the biggest change.",
      MetricArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/cloudwatch"])
def aws_cloudwatch_metric(a: MetricArgs) -> ToolOutput:
    def go():
        import datetime as dt
        end = dt.datetime.now(dt.timezone.utc)
        r = _client("cloudwatch", a.region).get_metric_data(
            MetricDataQueries=[{"Id": "m", "ReturnData": True, "MetricStat": {
                "Metric": {"Namespace": a.namespace, "MetricName": a.metric, "Dimensions": [{"Name": k, "Value": v} for k, v in a.dimensions.items()]},
                "Period": a.period_s, "Stat": a.stat}}],
            StartTime=end - dt.timedelta(minutes=a.minutes), EndTime=end, ScanBy="TimestampAscending")
        res = r["MetricDataResults"][0]
        pts = list(zip(res.get("Timestamps", []), res.get("Values", [])))
        if not pts:
            return "(no datapoints: wrong dimensions, or no data in window)"
        jumps = [(abs(pts[i][1] - pts[i - 1][1]), pts[i][0]) for i in range(1, len(pts))]
        return _j({"metric": f"{a.namespace}/{a.metric}", "stat": a.stat, "points": len(pts), "first": pts[0][1], "min": min(v for _, v in pts),
                   "max": max(v for _, v in pts), "last": pts[-1][1], "biggest_change_at": _iso(max(jumps)[1]) if jumps else None})
    return _run(go)


class AlarmArgs(Args):
    region: REGION = None
    state: str = Field("ALARM", pattern=r"^(ALARM|INSUFFICIENT_DATA|OK)$")


@tool("aws_cloudwatch_alarms", "CloudWatch alarms in a state (default ALARM) with metric, threshold, reason and state change time.",
      AlarmArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/cloudwatch"])
def aws_cloudwatch_alarms(a: AlarmArgs) -> ToolOutput:
    def go():
        al = _client("cloudwatch", a.region).describe_alarms(StateValue=a.state, MaxRecords=50)["MetricAlarms"]
        return "\n".join(_j({"alarm": x["AlarmName"], "metric": f"{x.get('Namespace')}/{x.get('MetricName')}", "threshold": x.get("Threshold"),
                             "comparison": x.get("ComparisonOperator"), "since": _iso(x.get("StateUpdatedTimestamp")),
                             "reason": (x.get("StateReason") or "")[:200]}) for x in al)
    return _run(go)


class LogsArgs(Args):
    region: REGION = None
    log_group: str = Field(pattern=r"^[A-Za-z0-9_/.#-]{1,512}$")
    filter_pattern: str = Field("", max_length=200, description='CloudWatch Logs filter pattern, for example "ERROR" or ?Exception ?Timeout')
    minutes: int = Field(30, ge=1, le=1440)
    limit: int = Field(100, ge=1, le=500)


@tool("aws_cloudwatch_logs", "Search one CloudWatch Logs group in a time window with a filter pattern. Logs are untrusted data.",
      LogsArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/logs/{a.log_group}"])
def aws_cloudwatch_logs(a: LogsArgs) -> ToolOutput:
    def go():
        start = int((time.time() - a.minutes * 60) * 1000)
        kw = {"logGroupName": a.log_group, "startTime": start, "limit": a.limit}
        if a.filter_pattern:
            kw["filterPattern"] = a.filter_pattern
        ev = _client("logs", a.region).filter_log_events(**kw)["events"]
        return "\n".join(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e['timestamp'] / 1000))}\t{e.get('logStreamName', '')[:40]}\t{e['message'][:400]}" for e in ev)
    return _run(go)


class TrailArgs(Args):
    region: REGION = None
    minutes: int = Field(180, ge=5, le=10080)
    resource_name: str | None = Field(None, max_length=200, description="For example an instance ID or security group ID")
    event_names: list[Annotated[str, Field(pattern=r"^[A-Za-z]{3,80}$")]] = Field(
        default_factory=list, max_length=10, description="Up to 10 event names in ONE call, for example [StopInstances, ModifyTargetGroup]")
    errors_only: bool = Field(False, description="Only calls that failed (errorCode set)")
    include_reads: bool = Field(False, description="Include read-only API calls (noisy)")


@tool("aws_cloudtrail_events",
      "CloudTrail change history: who changed what and when, and which API calls failed. Call it ONCE: without filters it "
      "returns recent write events; give up to 10 event_names in one call. Do not guess event names one call at a time.",
      TrailArgs, RiskLevel.READ, "aws", timeout_s=40, targets=lambda a: [f"aws:{a.region or 'default'}/cloudtrail"])
def aws_cloudtrail_events(a: TrailArgs) -> ToolOutput:
    def go():
        import datetime as dt
        end = dt.datetime.now(dt.timezone.utc)
        client = _client("cloudtrail", a.region)
        if a.resource_name:
            queries = [[{"AttributeKey": "ResourceName", "AttributeValue": a.resource_name}]]
        elif a.event_names:
            queries = [[{"AttributeKey": "EventName", "AttributeValue": n}] for n in a.event_names]
        elif not a.include_reads:
            queries = [[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]]
        else:
            queries = [[]]
        rows = []
        for attrs in queries:
            ev = client.lookup_events(LookupAttributes=attrs, StartTime=end - dt.timedelta(minutes=a.minutes),
                                      EndTime=end, MaxResults=50)["Events"]
            for e in ev:
                detail = json.loads(e.get("CloudTrailEvent") or "{}")
                if not a.include_reads and detail.get("readOnly"):
                    continue
                if a.errors_only and not detail.get("errorCode"):
                    continue
                rows.append((e["EventTime"], _j({"time": _iso(e["EventTime"]), "event": e["EventName"], "user": e.get("Username"),
                                                 "source_ip": detail.get("sourceIPAddress"), "error": detail.get("errorCode"),
                                                 "resources": [r.get("ResourceName") for r in e.get("Resources", [])][:5]})))
        rows.sort(key=lambda r: r[0], reverse=True)
        if not rows:
            return f"(no matching events in the last {a.minutes} min" + (f" for {a.event_names}" if a.event_names else "") + ")"
        return "\n".join(r for _, r in rows[:80])
    return _run(go)


class HealthArgs(Args):
    region: REGION = None
    trail_hours: int = Field(24, ge=1, le=168, description="CloudTrail look-back for failed write calls")


@tool("aws_health_summary",
      "START HERE for any AWS account or service question. One call: instance states (stopped with reason, failed status "
      "checks), load balancer target health per target group (unhealthy, unused targets resolved to their instance, empty "
      "target groups), CloudWatch alarms in ALARM, unattached EBS volumes, and failed CloudTrail write calls. Problems first.",
      HealthArgs, RiskLevel.READ, "aws", timeout_s=90, targets=lambda a: [f"aws:{a.region or 'default'}/summary"])
def aws_health_summary(a: HealthArgs) -> ToolOutput:
    def go():
        import datetime as dt
        ec2 = _client("ec2", a.region)
        insts = {}
        for r in ec2.describe_instances(MaxResults=1000)["Reservations"]:
            for i in r["Instances"]:
                insts[i["InstanceId"]] = i
        status = {}
        if insts:
            ids = list(insts)
            for k in range(0, len(ids), 100):
                for st in ec2.describe_instance_status(InstanceIds=ids[k:k + 100], IncludeAllInstances=True)["InstanceStatuses"]:
                    status[st["InstanceId"]] = (st.get("SystemStatus", {}).get("Status"), st.get("InstanceStatus", {}).get("Status"))
        problems, info = [], []
        states = {}
        for iid, i in insts.items():
            states[i["State"]["Name"]] = states.get(i["State"]["Name"], 0) + 1
            s_sys, s_inst = status.get(iid, (None, None))
            if s_sys not in (None, "ok", "not-applicable", "initializing") or s_inst not in (None, "ok", "not-applicable", "initializing"):
                problems.append(f"EC2 {iid} ({_name(i.get('Tags'))}): status checks system={s_sys} instance={s_inst}")
        info.append(f"EC2 instances: {states}")
        # load balancers and target health
        elb = _client("elbv2", a.region)
        lbs = [lb for page in elb.get_paginator("describe_load_balancers").paginate() for lb in page["LoadBalancers"]]
        empty, healthy_tgs, groups = [], 0, {}
        for lb in lbs:
            tgs = [tg for page in elb.get_paginator("describe_target_groups").paginate(LoadBalancerArn=lb["LoadBalancerArn"]) for tg in page["TargetGroups"]]
            for tg in tgs:
                th = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
                hc = f"{tg.get('HealthCheckProtocol')}:{tg.get('HealthCheckPort')}{tg.get('HealthCheckPath') or ''}"
                if not th:
                    empty.append(f"{lb['LoadBalancerName']}/{tg['TargetGroupName']}")
                    continue
                bad = [t for t in th if t["TargetHealth"]["State"] not in ("healthy",)]
                if not bad:
                    healthy_tgs += 1
                    continue
                for t in bad:
                    tid, port = t["Target"]["Id"], t["Target"].get("Port")
                    state, reason = t["TargetHealth"]["State"], t["TargetHealth"].get("Reason")
                    who = tid if len(tid) < 40 else "…" + tid[-40:]
                    if tid in insts:
                        inst = insts[tid]
                        who = f"instance {_name(inst.get('Tags'))} ({tid}, {inst['State']['Name']}"
                        if inst["State"]["Name"] != "running":
                            who += f": {(inst.get('StateReason') or {}).get('Message', '')}"
                        who += ")"
                    groups.setdefault((lb["LoadBalancerName"], who, state, reason), []).append(f"{tg['TargetGroupName']}:{port} [hc {hc}]")
        for (lbn, who, state, reason), items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            problems.append(f"{lbn}: {who} -> {len(items)} target group(s) {state} ({reason}): " + ", ".join(items[:6])
                            + (f" … +{len(items) - 6} more" if len(items) > 6 else ""))
        info.append(f"Load balancers: {len(lbs)}; target groups healthy: {healthy_tgs}; with no targets: {len(empty)}")
        if empty:
            info.append("Target groups with no targets (first 15): " + ", ".join(empty[:15]))
        cw = _client("cloudwatch", a.region)
        for al in cw.describe_alarms(StateValue="ALARM", MaxRecords=50)["MetricAlarms"]:
            problems.append(f"Alarm {al['AlarmName']} in ALARM since {_iso(al.get('StateUpdatedTimestamp'))}: {(al.get('StateReason') or '')[:120]}")
        vols = [v for page in ec2.get_paginator("describe_volumes").paginate(Filters=[{"Name": "status", "Values": ["available"]}]) for v in page["Volumes"]]
        if vols:
            gib = sum(v["Size"] for v in vols)
            info.append(f"Unattached EBS volumes: {len(vols)} ({gib} GiB) — cost, not an outage")
        end = dt.datetime.now(dt.timezone.utc)
        ct = _client("cloudtrail", a.region)
        ev, token = [], None
        for _ in range(6):   # up to 300 write events
            kw = dict(LookupAttributes=[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
                      StartTime=end - dt.timedelta(hours=a.trail_hours), EndTime=end, MaxResults=50)
            if token:
                kw["NextToken"] = token
            page = ct.lookup_events(**kw)
            ev += page["Events"]
            token = page.get("NextToken")
            if not token:
                break
        info.append(f"CloudTrail write events read: {len(ev)}" + (" (more exist, not read)" if token else ""))
        failed = {}
        for e in ev:
            d = json.loads(e.get("CloudTrailEvent") or "{}")
            code = d.get("errorCode")
            if code and not str(code).endswith("DryRunOperation"):
                key = f"{e['EventName']} -> {code}"
                failed[key] = failed.get(key, 0) + 1
        for k, n in failed.items():
            problems.append(f"CloudTrail: {k} ({n}x in the last {a.trail_hours} h)")
        out = ["PROBLEMS (" + str(len(problems)) + "):"] + [f"- {p}" for p in problems] + ["INFO:"] + [f"- {x}" for x in info]
        return "\n".join(out)
    return _run(go)


# ---------- EKS / IAM / Route 53 ----------

class EksArgs(Args):
    region: REGION = None
    cluster: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


@tool("aws_eks_cluster", "EKS cluster status, version, endpoint access, and node groups with status, scaling config and health issues.",
      EksArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:{a.region or 'default'}/eks/{a.cluster}"])
def aws_eks_cluster(a: EksArgs) -> ToolOutput:
    def go():
        eks = _client("eks", a.region)
        c = eks.describe_cluster(name=a.cluster)["cluster"]
        out = [_j({"cluster": c["name"], "status": c["status"], "version": c.get("version"), "endpoint_public": c.get("resourcesVpcConfig", {}).get("endpointPublicAccess"),
                   "endpoint_private": c.get("resourcesVpcConfig", {}).get("endpointPrivateAccess"), "health": c.get("health")})]
        for ng in eks.list_nodegroups(clusterName=a.cluster)["nodegroups"][:20]:
            n = eks.describe_nodegroup(clusterName=a.cluster, nodegroupName=ng)["nodegroup"]
            out.append(_j({"nodegroup": ng, "status": n["status"], "scaling": n.get("scalingConfig"), "instance_types": n.get("instanceTypes"),
                           "issues": [(i.get("code"), i.get("message")) for i in (n.get("health") or {}).get("issues", [])]}))
        return "\n".join(out)
    return _run(go)


class SimArgs(Args):
    region: REGION = None
    principal_arn: str = Field(pattern=r"^arn:aws[a-z-]*:iam::\d{12}:(role|user)/[A-Za-z0-9+=,.@_/-]{1,128}$")
    actions: list[Annotated[str, Field(pattern=r"^[a-z0-9-]+:[A-Za-z0-9*]+$")]] = Field(min_length=1, max_length=10)
    resource_arns: list[Annotated[str, Field(max_length=512)]] = Field(default_factory=lambda: ["*"], max_length=10)


@tool("aws_iam_simulate", "Simulate IAM: is this role/user allowed to do these actions on these resources? Shows allowed / explicitDeny / implicitDeny and the matching statements.",
      SimArgs, RiskLevel.READ, "aws", targets=lambda a: ["aws:iam"])
def aws_iam_simulate(a: SimArgs) -> ToolOutput:
    def go():
        r = _client("iam", a.region).simulate_principal_policy(PolicySourceArn=a.principal_arn, ActionNames=a.actions, ResourceArns=a.resource_arns)
        return "\n".join(_j({"action": x["EvalActionName"], "resource": x.get("EvalResourceName"), "decision": x["EvalDecision"],
                             "matched": [s.get("SourcePolicyId") for s in x.get("MatchedStatements", [])],
                             "missing_context": x.get("MissingContextValues") or None}) for x in r["EvaluationResults"])
    return _run(go)


class DnsArgs(Args):
    zone_id: str = Field(pattern=r"^Z[A-Z0-9]{5,32}$")
    name: str = Field(pattern=r"^[A-Za-z0-9.*_-]{1,253}$")


@tool("aws_route53_records", "Route 53 records for one name in a hosted zone (type, TTL, values or alias target, routing policy).",
      DnsArgs, RiskLevel.READ, "aws", targets=lambda a: [f"aws:route53/{a.zone_id}"])
def aws_route53_records(a: DnsArgs) -> ToolOutput:
    def go():
        name = a.name if a.name.endswith(".") else a.name + "."
        rr = _client("route53", None).list_resource_record_sets(HostedZoneId=a.zone_id, StartRecordName=name, MaxItems="20")["ResourceRecordSets"]
        rr = [r for r in rr if r["Name"].rstrip(".") == name.rstrip(".")]
        return "\n".join(_j({"name": r["Name"], "type": r["Type"], "ttl": r.get("TTL"), "values": [v["Value"] for v in r.get("ResourceRecords", [])],
                             "alias": (r.get("AliasTarget") or {}).get("DNSName"), "policy": r.get("SetIdentifier")}) for r in rr)
    return _run(go)


# ---------- masking: every AWS tool output is pseudonymized; placeholders in inputs are restored ----------

def _wrap_masking() -> None:
    from ..masking import MASKER
    from .base import TOOLS

    def wrap(t):
        fn = t.fn

        def masked(a):
            if os.environ.get("NIGHTWATCH_AWS_MASK", "1") == "0":
                return fn(a)
            real = t.input_model.model_validate(MASKER.unmask(a.model_dump(mode="json")))
            out = fn(real)
            out.text = MASKER.mask(out.text)
            return out
        t.fn = masked

    for t in TOOLS.values():
        if t.connector == "aws" and not getattr(t.fn, "_masked", False):
            wrap(t)
            t.fn._masked = True


_wrap_masking()
