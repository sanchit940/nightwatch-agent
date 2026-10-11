"""Slack notifications for incidents and approvals, written in ASD-STE100 style.

One thread per incident, in the channel of its severity (policy.yaml: slack.channels).
- The first message is the plan: what NightWatch checked, what it found, the cause, the plan, risk, rollback and
  verification, with Approve / Reject / Suggest another fix buttons.
- Later messages (decisions, execution, verification, new plans, final result) go into the same thread.
Without a bot token the notifier runs in dry-run mode: it writes each payload to runs/slack-outbox.jsonl and
prints a text preview, so the whole flow can be tested without a Slack workspace.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..audit import Audit
from ..config import ROOT

SEV_EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡"}
MAX_SECTION = 2900


# ---------- ASD-STE100 text helpers ----------

def ste(text: str | None, max_sentences: int = 1, max_words: int = 18) -> str:
    """ASD-STE100 style for model text: few short sentences, no semicolons, each sentence ends with a period."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"\s*;\s*", ". ", text)
    # A plain word after a full stop starts a sentence: capitalize it (names like nw-redis are not plain words).
    text = re.sub(r"(?<=\. )([a-z]+)(?=[\s.,:])", lambda m: m.group(1).capitalize(), text)
    text = re.sub(r"\s*—\s*|\s+-\s+", ". ", text)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])", text) if x.strip()]
    out = []
    for snt in sentences[:max_sentences]:
        words = snt.split(" ")
        snt = " ".join(words[:max_words]).rstrip(",:") + (" …" if len(words) > max_words else "")
        if snt and snt[-1] not in ".!?…":
            snt += "."
        first = snt.split(" ", 1)[0]
        if first.isalpha() and first.islower():   # keep names such as nw-redis, i-0abc, k8s as written
            snt = snt[0].upper() + snt[1:]
        out.append(snt)
    return " ".join(out)


def _res(ok: bool, code: str | None) -> str:
    return "OK" if ok else f"failed ({code or 'error'})"


def describe_tool_call(tool: str, a: dict, ok: bool | None = None, code: str | None = None) -> str:
    """One STE sentence for one investigation step."""
    src = a.get("source", "agent")
    src_txt = "from the NightWatch host" if src == "agent" else f"from {src}"
    result = "" if ok is None else f": {_res(ok, code)}"
    m = {
        "net_http_check": lambda: f"Sent GET {a.get('url')} {src_txt}{result}.",
        "net_trace_path": lambda: f"Traced the request path to {a.get('url')} {src_txt}{result}.",
        "net_dns_resolve": lambda: f"Resolved the name {a.get('host')} {src_txt}{result}.",
        "net_tcp_connect": lambda: f"Opened a TCP connection to {a.get('host')}:{a.get('port')} {src_txt}{result}.",
        "net_tls_check": lambda: f"Checked TLS on {a.get('host')}:{a.get('port')}{result}.",
        "docker_list_containers": lambda: f"Listed all containers{result}.",
        "docker_inspect": lambda: f"Inspected container {a.get('container')}{result}.",
        "docker_logs": lambda: f"Read the logs of {a.get('container')} (last {a.get('since_minutes', 30)} min){result}.",
        "docker_events": lambda: f"Read Docker change events (last {a.get('since_minutes', 60)} min){result}.",
        "docker_stats": lambda: f"Read CPU and memory use of all containers{result}.",
        "docker_network_inspect": lambda: f"Inspected network {a.get('network')}{result}.",
        "linux_check": lambda: f"Ran the Linux check '{a.get('check')}' on {a.get('target')}{result}.",
        "linux_use_snapshot": lambda: f"Ran a USE snapshot (CPU, memory, disk, limits) on {a.get('target')}{result}.",
        "k8s_get": lambda: f"Listed {a.get('kind')} in {a.get('namespace') or 'all namespaces'}{result}.",
        "k8s_describe": lambda: f"Described {a.get('kind')} {a.get('name')} in {a.get('namespace')}{result}.",
        "k8s_events": lambda: f"Read Kubernetes events in {a.get('namespace') or 'all namespaces'}{result}.",
        "k8s_logs": lambda: f"Read the logs of pod {a.get('pod')}{' (previous instance)' if a.get('previous') else ''}{result}.",
        "k8s_rollout_history": lambda: f"Read the rollout history of {a.get('namespace')}/{a.get('deployment')}{result}.",
        "k8s_revision_diff": lambda: f"Compared revisions {a.get('from_revision')} and {a.get('to_revision')} of {a.get('deployment')}{result}.",
        "k8s_auth_can_i": lambda: f"Checked RBAC: {a.get('verb')} {a.get('resource')} in {a.get('namespace')}{result}.",
        "prom_query": lambda: f"Queried Prometheus: {a.get('promql')}{result}.",
        "prom_query_range": lambda: f"Queried Prometheus over {a.get('minutes')} min: {a.get('promql')}{result}.",
        "prom_alerts": lambda: f"Read the active Prometheus alerts{result}.",
        "prom_targets": lambda: f"Read the Prometheus scrape targets{result}.",
        "get_runbook": lambda: f"Read the runbook '{a.get('topic')}'{result}.",
        "linux_service_status": lambda: f"Read the status and journal of {a.get('unit')} on {a.get('target')}{result}.",
        "grafana_alerts": lambda: f"Read the Grafana alert rules{result}.",
        "grafana_dashboards": lambda: f"Searched Grafana dashboards{result}.",
        "grafana_dashboard_panels": lambda: f"Read the panels of dashboard {a.get('uid')}{result}.",
        "grafana_annotations": lambda: f"Read Grafana annotations (last {a.get('minutes')} min){result}.",
        "loki_query": lambda: f"Searched logs with LogQL: {a.get('logql')}{result}.",
        "loki_labels": lambda: f"Listed Loki labels{result}.",
        "neatlogs_traces": lambda: f"Listed AI-agent traces in neatlogs{(' with status ' + a['status']) if a.get('status') else ''}{result}.",
        "neatlogs_trace_spans": lambda: f"Read the spans of trace {a.get('trace_id')}{result}.",
        "neatlogs_search": lambda: f"Searched neatlogs traces for '{a.get('query')}'{result}.",
        "neatlogs_alerts": lambda: f"Read the neatlogs alert history{result}.",
        "aws_ec2_instances": lambda: f"Read EC2 instances and status checks{result}.",
        "aws_ebs_volumes": lambda: f"Read EBS volumes{result}.",
        "aws_security_groups": lambda: f"Read security group rules {', '.join(a.get('group_ids', []))}{result}.",
        "aws_vpc_network": lambda: f"Read routes, NACLs and gateways for {a.get('subnet_id') or a.get('vpc_id')}{result}.",
        "aws_elb_health": lambda: f"Read load balancer target health{result}.",
        "aws_cloudwatch_metric": lambda: f"Read CloudWatch {a.get('namespace')}/{a.get('metric')}{result}.",
        "aws_cloudwatch_alarms": lambda: f"Read CloudWatch alarms{result}.",
        "aws_cloudwatch_logs": lambda: f"Searched CloudWatch logs {a.get('log_group')}{result}.",
        "aws_cloudtrail_events": lambda: f"Read CloudTrail changes (last {a.get('minutes')} min){result}.",
        "aws_eks_cluster": lambda: f"Read EKS cluster {a.get('cluster')}{result}.",
        "aws_iam_simulate": lambda: f"Simulated IAM permissions for {a.get('principal_arn')}{result}.",
        "aws_route53_records": lambda: f"Read DNS records for {a.get('name')}{result}.",
    }
    return m.get(tool, lambda: f"Ran {tool}{result}.")()


def describe_action(action: str, p: dict) -> str:
    m = {
        "restart_container": lambda: f"Restart container {p.get('container')}.",
        "start_container": lambda: f"Start container {p.get('container')}.",
        "connect_network": lambda: f"Connect container {p.get('container')} to network {p.get('network')}"
                                   + (f" with the DNS name '{p['alias']}'." if p.get("alias") else "."),
        "rollout_undo": lambda: f"Roll back deployment {p.get('namespace')}/{p.get('deployment')} to "
                                + (f"revision {p['to_revision']}." if p.get("to_revision") else "the previous revision."),
        "rollout_restart": lambda: f"Restart all pods of deployment {p.get('namespace')}/{p.get('deployment')}, one by one.",
        "scale_deployment": lambda: f"Set deployment {p.get('namespace')}/{p.get('deployment')} to {p.get('replicas')} replicas.",
    }
    return m.get(action, lambda: f"Run {action} with {json.dumps(p)}.")()


def describe_check(c: dict) -> str:
    exp = c.get("expect") or {"ok": True}
    base = describe_tool_call(c.get("tool", ""), c.get("args") or {}).rstrip(".")
    for past, now in (("Sent", "Send"), ("Traced", "Trace"), ("Resolved", "Resolve"), ("Opened", "Open"), ("Checked", "Check"),
                      ("Listed", "List"), ("Inspected", "Inspect"), ("Ran", "Run"), ("Described", "Describe"),
                      ("Queried", "Query"), ("Compared", "Compare")):
        if base.startswith(past + " "):
            base = now + base[len(past):]
            break
    parts = []
    if "ok" in exp:
        parts.append("it succeeds" if exp["ok"] else "it fails")
    if exp.get("contains"):
        parts.append(f"the output contains '{exp['contains']}'")
    if exp.get("not_contains"):
        parts.append(f"the output does not contain '{exp['not_contains']}'")
    if exp.get("regex"):
        parts.append(f"the output matches /{exp['regex']}/")
    return f"{base}. Expect: {' and '.join(parts) or 'success'}."


EFFECT = {"removes_root_cause": "This removes the cause.",
          "restores_service_temporarily": "This restores service for now. The permanent fix needs a human."}


def steps_from_audit(audit: Audit, iid: str, limit: int = 10, only_new: bool = False) -> list[str]:
    """STE lines for the investigation steps. only_new: only the steps after the last Slack message of this incident."""
    seen, out = set(), []
    start = audit.get_meta(iid, "slack_steps_posted", 0) if only_new else 0
    for e in audit.events(iid):
        if e["kind"] != "tool_call":
            continue
        p = e["payload"]
        line = describe_tool_call(p["tool"], p.get("args") or {}, p.get("ok"), p.get("error_code"))
        if line not in seen:
            seen.add(line)
            out.append(line)
    total = len(out)
    out = out[start:]
    audit.set_meta(iid, "slack_steps_posted", total)
    if len(out) > limit:
        out = out[:limit] + [f"… and {len(out) - limit} more steps (see the audit log)."]
    return out


# ---------- Block Kit builders (short ASD-STE100 messages) ----------

NOT_DONE_REASON = {
    "STALE_STATE": "The target changed after the approval.",
    "STALE_APPROVAL": "The approval expired.",
    "REPLAY": "This approval was already used.",
    "MODIFIED_APPROVAL": "The approval record was changed.",
    "BAD_SIGNATURE": "The approval signature is not valid.",
    "NOT_APPROVED": "Nobody approved the plan.",
    "OUT_OF_SCOPE": "The target is outside the allowed scope.",
    "NOT_ALLOWED": "The policy does not allow this action now.",
}
RESULT = {"resolved": "✅ Resolved", "partially_resolved": "⚠️ Partly resolved", "unresolved": "❌ Not resolved",
          "awaiting_approval": "⏳ Waiting for a decision", "fix_rejected": "❌ Plan rejected. A human must act",
          "investigated": "🧭 Investigated. No change made"}


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"• {l}" for l in lines if l)


def _utc(ts: float) -> str:
    return time.strftime("%H:%M UTC", time.gmtime(ts))


def _text(lines: list[str]) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(l for l in lines if l)[:MAX_SECTION]}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _sev(report: dict, rec: dict | None = None) -> str:
    return (report.get("severity") or {}).get("level") or ((rec or {}).get("justification") or {}).get("severity") or "P1"


def _problem(report: dict) -> str:
    ps = report.get("problem_statement") or {}
    return ste(ps.get("symptom") or report.get("summary"), 1, 18)


def _cause(report: dict) -> str:
    rc = report.get("root_cause") or {}
    if not rc:
        return "Not known yet."
    return f"{ste(rc.get('description'), 1, 18)} (`{rc.get('category', '?')}`, confidence {rc.get('confidence', '?')})"


def plan_message(iid: str, report: dict, rec: dict, mention: str = "", in_thread: bool = False) -> dict:
    """Main approval message: about 9 short lines. Evidence goes to a Details reply in the thread."""
    j = rec.get("justification") or {}
    sev = _sev(report, rec)
    action_txt = describe_action(rec["action"], rec["params"])
    checks = j.get("verification") or []
    check_txt = (describe_check(checks[0]) + (f" (+{len(checks) - 1} more)" if len(checks) > 1 else "")) if checks else "-"
    lines = [mention if mention and not in_thread else "",
             f"*Problem:* {_problem(report)}" if not in_thread else "",
             f"*Cause:* {_cause(report)}" if not in_thread else "",
             f"*Plan:* {action_txt}",
             f"*Why:* {ste(j.get('reason'), 1, 18)}" if in_thread else "",
             f"*Effect:* {EFFECT[j['expected_effect']]}" if j.get("expected_effect") in EFFECT else "",
             f"*Risk:* {ste(j.get('risk'), 1, 15)}",
             f"*Rollback:* {ste(j.get('rollback'), 1, 15)}",
             f"*Check after the change:* {check_txt}"]
    title = f"{SEV_EMOJI.get(sev, '⚪')} {sev} · {'New plan' if in_thread else 'Approval needed'}"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title}}, _text(lines),
              {"type": "actions", "block_id": f"decide:{rec['id']}", "elements": [
                  {"type": "button", "action_id": "nw_approve", "style": "primary", "value": rec["id"],
                   "text": {"type": "plain_text", "text": "Approve"},
                   "confirm": {"title": {"type": "plain_text", "text": "Approve this change?"},
                               "text": {"type": "mrkdwn", "text": f"{action_txt}\nThe approval is for the current state only. It expires at {_utc(rec['expires_at'])}."},
                               "confirm": {"type": "plain_text", "text": "Approve"}, "deny": {"type": "plain_text", "text": "Cancel"}}},
                  {"type": "button", "action_id": "nw_reject", "style": "danger", "value": rec["id"], "text": {"type": "plain_text", "text": "Reject"}},
                  {"type": "button", "action_id": "nw_suggest", "value": json.dumps({"iid": iid, "aid": rec["id"]}),
                   "text": {"type": "plain_text", "text": "Suggest another fix"}}]},
              _context(f"`{iid}` · plan `{rec['id']}` · expires {_utc(rec['expires_at'])}" + ("" if in_thread else " · details in thread"))]
    return {"text": f"{sev} approval needed: {action_txt}", "blocks": blocks}


def details_message(report: dict, audit: Audit, iid: str) -> dict:
    """One thread reply with the evidence. Short lists only."""
    calls = [e["payload"] for e in audit.events(iid) if e["kind"] == "tool_call"]
    failed = [describe_tool_call(c["tool"], c.get("args") or {}, False, c.get("error_code")) for c in calls if not c.get("ok")]
    unique_failed = list(dict.fromkeys(failed))
    checked = f"{len(calls)} checks. {len(calls) - len(failed)} OK. {len(failed)} failed." if failed else f"{len(calls)} checks. All OK."
    found = [ste(e.get("finding"), 1, 18) for e in (report.get("evidence") or [])[:4]]
    rc = report.get("root_cause") or {}
    symptoms = [ste(x, 1, 12) for x in (rc.get("symptoms_not_causes") or [])[:3]]
    unknown = [ste(x, 1, 15) for x in (report.get("known_unknowns") or [])[:2]]
    lines = ["*Details*", f"*Checked:* {checked}"] + ([_bullets(unique_failed[:3])] if failed else [])
    if found:
        lines += ["*Found:*", _bullets(found)]
    if symptoms:
        lines += ["*Symptoms, not causes:*", _bullets(symptoms)]
    if unknown:
        lines += ["*Not known:*", _bullets(unknown)]
    return {"text": "Details", "blocks": [_text(lines)]}


def human_needed_message(iid: str, report: dict, mention: str = "", in_thread: bool = False) -> dict:
    sev = _sev(report)
    manual = [ste(r.get("description"), 1, 18) for r in (report.get("recommendations") or []) if r.get("type") != "proposed_action"][:2]
    lines = [mention if mention and not in_thread else "",
             f"*Problem:* {_problem(report)}",
             f"*Cause:* {_cause(report)}",
             "*Plan:* No allowed action can fix this safely.",
             ("*Next step:*\n" + _bullets(manual)) if manual else ""]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{SEV_EMOJI.get(sev, '⚪')} {sev} · A human must act"}}, _text(lines),
              {"type": "actions", "block_id": f"instruct:{iid}", "elements": [
                  {"type": "button", "action_id": "nw_instruct", "style": "primary", "value": json.dumps({"iid": iid, "aid": None}),
                   "text": {"type": "plain_text", "text": "Give instructions"}}]},
              _context(f"`{iid}` · fix it by hand and tell me to verify, or give an instruction" + ("" if in_thread else " · details in thread"))]
    return {"text": f"{sev} {iid}: a human must act", "blocks": blocks}


def final_message(iid: str, report: dict, status: str) -> dict:
    follow = [ste(r.get("description"), 1, 15) for r in (report.get("recommendations") or [])
              if r.get("type") in ("manual", "follow_up")][:2]
    lines = [f"*{RESULT.get(status, status)}.* {ste(report.get('summary'), 1, 18)}"]
    if follow:
        lines += ["*Follow-up:*", _bullets(follow)]
    return {"text": f"{iid}: {RESULT.get(status, status)}", "blocks": [_text(lines), _context(f"Audit: `nightwatch audit --incident {iid}`")]}


def execution_message(action_txt: str, ex: dict) -> dict:
    v = ex.get("verification") or {}
    if not ex.get("executed"):
        reason = NOT_DONE_REASON.get(ex.get("code"), f"Code {ex.get('code')}.")
        return {"text": f"⛔ *Not done:* {action_txt} {reason} NightWatch made no change."}
    if not ex.get("ok"):
        return {"text": f"❌ *Failed:* {action_txt} Error `{ex.get('code')}`. NightWatch checks again."}
    checks = v.get("checks") or []
    failed = [c for c in checks if not c.get("passed")]
    line = f"✅ *Done:* {action_txt} " + (f"Check: {v.get('passed', 0)} of {v.get('total', 0)} passed." if v.get("total") else "No check was defined.")
    if failed:
        line += "\n" + _bullets([f"Failed: {describe_check(c)}" for c in failed[:3]])
    return {"text": line}


# ---------- notifier ----------

class SlackNotifier:
    def __init__(self, cfg: dict, audit: Audit, client: Any | None = None, outbox: Path | None = None, console: Any | None = None):
        self.cfg = cfg or {}
        self.audit = audit
        self.client = client
        self.outbox = outbox or (ROOT / "runs" / "slack-outbox.jsonl")
        self.console = console

    @property
    def dry_run(self) -> bool:
        return self.client is None

    def _channel(self, sev: str) -> str:
        return (self.cfg.get("channels") or {}).get(sev) or (self.cfg.get("channels") or {}).get("P1") or "#nightwatch-alerts"

    def _mention(self, sev: str) -> str:
        return (self.cfg.get("mention") or {}).get(sev, "")

    def _approvers(self, sev: str) -> list[str]:
        return (self.cfg.get("approvers") or {}).get(sev) or []

    def _send(self, channel: str, payload: dict, thread_ts: str | None = None) -> tuple[str, str]:
        if self.dry_run:
            ts = f"{time.time():.6f}"
            with self.outbox.open("a") as f:
                f.write(json.dumps({"method": "chat.postMessage", "channel": channel, "thread_ts": thread_ts, "ts": ts, **payload}) + "\n")
            if self.console:
                self.console.print(f"[magenta]── Slack (dry run) → {channel}{' (thread)' if thread_ts else ''}[/]\n{render_text(payload)}")
            return channel, ts
        r = self.client.chat_postMessage(channel=channel, thread_ts=thread_ts, unfurl_links=False, **payload)
        return r["channel"], r["ts"]

    def update(self, channel: str, ts: str, payload: dict) -> None:
        if self.dry_run:
            with self.outbox.open("a") as f:
                f.write(json.dumps({"method": "chat.update", "channel": channel, "ts": ts, **payload}) + "\n")
            return
        self.client.chat_update(channel=channel, ts=ts, **payload)

    def _thread(self, iid: str, sev: str, first_payload: dict) -> tuple[str, str, bool]:
        """Return (channel, thread_ts, created). The first message of an incident opens the thread."""
        t = self.audit.get_meta(iid, "slack_thread")
        if t:
            return t["channel"], t["ts"], False
        ch, ts = self._send(self._channel(sev), first_payload)
        self.audit.set_meta(iid, "slack_thread", {"channel": ch, "ts": ts, "severity": sev})
        self.audit.log("slack_posted", {"channel": ch, "ts": ts, "kind": "thread_start"}, iid)
        return ch, ts, True

    def post_plan(self, iid: str, report: dict, rec: dict) -> None:
        sev = _sev(report, rec)
        existing = self.audit.get_meta(iid, "slack_thread")
        payload = plan_message(iid, report, rec, self._mention(sev), in_thread=bool(existing))
        if existing:
            ch, ts = self._send(existing["channel"], payload, existing["ts"])
        else:
            ch, ts, _ = self._thread(iid, sev, payload)
            self._send(ch, details_message(report, self.audit, iid), ts)
        self.audit.set_meta(iid, f"slack_msg:{rec['id']}", {"channel": ch, "ts": ts, "text": payload["text"], "blocks": payload["blocks"]})

    def post_human_needed(self, iid: str, report: dict) -> None:
        sev = _sev(report)
        existing = self.audit.get_meta(iid, "slack_thread")
        payload = human_needed_message(iid, report, self._mention(sev), in_thread=bool(existing))
        if existing:
            self._send(existing["channel"], payload, existing["ts"])
        else:
            ch, ts, _ = self._thread(iid, sev, payload)
            self._send(ch, details_message(report, self.audit, iid), ts)

    def post_final(self, iid: str, report: dict, status: str) -> None:
        existing = self.audit.get_meta(iid, "slack_thread")
        payload = final_message(iid, report, status)
        if existing:
            self._send(existing["channel"], payload, existing["ts"])
        else:   # nothing was posted before (for example resolved without a change): one short message
            self._thread(iid, _sev(report), payload)

    def post_update(self, iid: str, text: str) -> None:
        t = self.audit.get_meta(iid, "slack_thread")
        if not t:
            return
        self._send(t["channel"], {"text": text[:MAX_SECTION]}, t["ts"])

    def close_buttons(self, iid: str, approval_id: str, status_line: str) -> None:
        """Replace the buttons of a plan message with the decision, so nobody can click twice."""
        m = self.audit.get_meta(iid, f"slack_msg:{approval_id}")
        if not m:
            return
        blocks = [b for b in m.get("blocks", []) if b.get("type") != "actions"]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": status_line[:MAX_SECTION]}})
        self.update(m["channel"], m["ts"], {"text": m.get("text", status_line), "blocks": blocks})


def render_text(payload: dict) -> str:
    """Plain-text preview of a Block Kit payload (dry run and logs)."""
    out = []
    for b in payload.get("blocks") or []:
        t = b.get("type")
        if t == "header":
            out.append(f"## {b['text']['text']}")
        elif t == "section":
            if b.get("text"):
                out.append(b["text"]["text"])
            for f in b.get("fields") or []:
                out.append(f["text"].replace("\n", ": ", 1))
        elif t == "actions":
            out.append("[ " + " ] [ ".join(e["text"]["text"] for e in b["elements"]) + " ]")
        elif t == "context":
            out.append("  " + " ".join(e.get("text", "") for e in b["elements"]))
    if not out:
        out.append(payload.get("text", ""))
    return "\n".join(out)
