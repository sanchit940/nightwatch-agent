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

def ste(text: str | None, max_sentences: int = 2, max_words: int = 25) -> str:
    """Keep a model-written text short: at most N sentences, each at most N words."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])", text)
    out = []
    for s in sentences[:max_sentences]:
        words = s.split(" ")
        out.append(" ".join(words[:max_words]) + (" …" if len(words) > max_words else ""))
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


def steps_from_audit(audit: Audit, iid: str, limit: int = 10) -> list[str]:
    seen, out = set(), []
    for e in audit.events(iid):
        if e["kind"] != "tool_call":
            continue
        p = e["payload"]
        line = describe_tool_call(p["tool"], p.get("args") or {}, p.get("ok"), p.get("error_code"))
        if line not in seen:
            seen.add(line)
            out.append(line)
    if len(out) > limit:
        out = out[:limit] + [f"… and {len(out) - limit} more steps (see the audit log)."]
    return out


# ---------- Block Kit builders ----------

def _bullets(lines: list[str]) -> str:
    return "\n".join(f"• {l}" for l in lines if l)


def _section(title: str, body: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{body}"[:MAX_SECTION]}}


def _utc(ts: float) -> str:
    return time.strftime("%H:%M UTC", time.gmtime(ts))


def incident_overview(report: dict) -> list[dict]:
    rc = report.get("root_cause") or {}
    ps = report.get("problem_statement") or {}
    sev = (report.get("severity") or {})
    blocks = [
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Severity*\n{sev.get('level', '?')}: {ste(sev.get('reason'), 1)}"},
            {"type": "mrkdwn", "text": f"*Service*\n{ste(ps.get('affected_service'), 1) or '-'}"},
            {"type": "mrkdwn", "text": f"*Symptom*\n{ste(ps.get('symptom'), 1) or '-'}"},
            {"type": "mrkdwn", "text": f"*Started*\n{ps.get('start_time') or '-'}"},
        ]},
    ]
    return blocks


def findings_blocks(report: dict, steps: list[str]) -> list[dict]:
    rc = report.get("root_cause") or {}
    blocks = []
    if steps:
        blocks.append(_section("What I checked", _bullets(steps)))
    ev = [ste(e.get("finding"), 1) for e in (report.get("evidence") or [])[:6]]
    if ev:
        blocks.append(_section("What I found", _bullets(ev)))
    cause = (f"`{rc.get('category', '?')}` on *{rc.get('resource', '?')}* (confidence: {rc.get('confidence', '?')}).\n"
             f"{ste(rc.get('description'), 3)}")
    blocks.append(_section("Cause", cause))
    if rc.get("symptoms_not_causes"):
        blocks.append(_section("Symptoms, not causes", _bullets([ste(s, 1) for s in rc["symptoms_not_causes"][:5]])))
    return blocks


def plan_message(iid: str, report: dict, rec: dict, steps: list[str], mention: str = "", approvers: list[str] | None = None,
                 in_thread: bool = False) -> dict:
    j = rec.get("justification") or {}
    sev = (report.get("severity") or {}).get("level") or j.get("severity") or "P1"
    action_txt = describe_action(rec["action"], rec["params"])
    title = f"{SEV_EMOJI.get(sev, '⚪')} {sev} · Approval needed · {iid}"
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}}]
    lead = f"{mention} " if mention and not in_thread else ""
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"{lead}{ste(report.get('summary'), 2)}"}})
    if not in_thread:
        blocks += incident_overview(report)
    blocks += findings_blocks(report, steps)
    plan = [f"*{action_txt}*", EFFECT.get(j.get("expected_effect"), ""),
            f"Why: {ste(j.get('reason'), 2)}", f"Risk: {ste(j.get('risk'), 2)}", f"Rollback: {ste(j.get('rollback'), 2)}"]
    blocks.append(_section("Plan", "\n".join(l for l in plan if l and not l.endswith(": "))))
    checks = [describe_check(c) for c in (j.get("verification") or [])[:5]]
    if checks:
        blocks.append(_section("After the change, I will verify", _bullets(checks)))
    unknown = [ste(u, 1) for u in (report.get("known_unknowns") or [])[:4]]
    if unknown:
        blocks.append(_section("Not known", _bullets(unknown)))
    blocks.append({"type": "actions", "block_id": f"decide:{rec['id']}", "elements": [
        {"type": "button", "action_id": "nw_approve", "style": "primary", "value": rec["id"],
         "text": {"type": "plain_text", "text": "Approve"},
         "confirm": {"title": {"type": "plain_text", "text": "Approve this exact change?"},
                     "text": {"type": "mrkdwn", "text": f"{action_txt}\nThe approval is bound to the current state of the target. "
                                                         f"It expires at {_utc(rec['expires_at'])}."},
                     "confirm": {"type": "plain_text", "text": "Approve"}, "deny": {"type": "plain_text", "text": "Cancel"}}},
        {"type": "button", "action_id": "nw_reject", "style": "danger", "value": rec["id"],
         "text": {"type": "plain_text", "text": "Reject"}},
        {"type": "button", "action_id": "nw_suggest", "value": json.dumps({"iid": iid, "aid": rec["id"]}),
         "text": {"type": "plain_text", "text": "Suggest another fix"}},
    ]})
    who = ("Approvers: " + ", ".join(f"<@{u}>" for u in approvers)) if approvers else "Any channel member can decide."
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        f"Approval `{rec['id']}` · bound to state `{str(rec.get('fingerprint'))[:40]}` · expires {_utc(rec['expires_at'])} · {who}"}]})
    return {"text": f"{sev} approval needed: {action_txt}", "blocks": blocks}


def report_message(iid: str, report: dict, steps: list[str], needs_human: bool, mention: str = "", in_thread: bool = False) -> dict:
    sev = (report.get("severity") or {}).get("level", "P1")
    res = report.get("resolution") or "not_attempted"
    state = {"resolved": "✅ Resolved", "partially_resolved": "⚠️ Partly resolved", "unresolved": "❌ Not resolved"}.get(res, "🧭 Investigated")
    title = f"{SEV_EMOJI.get(sev, '⚪')} {sev} · {state if not needs_human else 'A human must act'} · {iid}"
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}}]
    lead = f"{mention} " if mention and not in_thread else ""
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"{lead}{ste(report.get('summary'), 2)}"}})
    if not in_thread:
        blocks += incident_overview(report)
    blocks += findings_blocks(report, steps)
    recs = [f"{'[manual] ' if r.get('type') == 'manual' else ''}{ste(r.get('description'), 2)}"
            for r in (report.get("recommendations") or [])[:5]]
    if recs:
        blocks.append(_section("Next steps", _bullets(recs)))
    if needs_human:
        blocks.append({"type": "actions", "block_id": f"instruct:{iid}", "elements": [
            {"type": "button", "action_id": "nw_instruct", "style": "primary", "value": json.dumps({"iid": iid, "aid": None}),
             "text": {"type": "plain_text", "text": "Give instructions"}}]})
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
            "No allowed action can fix this safely. Fix it by hand and tell me to verify, or give me an instruction."}]})
    return {"text": f"{sev} {iid}: {state}", "blocks": blocks}


def execution_message(action_txt: str, ex: dict) -> dict:
    v = ex.get("verification") or {}
    lines = []
    if not ex.get("executed"):
        lines.append(f"⛔ *Not executed:* {action_txt}")
        lines.append(f"Reason: `{ex.get('code')}`. {ste(ex.get('detail'), 2)}")
        lines.append("NightWatch made no change.")
    else:
        lines.append(f"{'✅' if ex.get('ok') else '❌'} *Executed:* {action_txt} Result: {'OK' if ex.get('ok') else ex.get('code')}.")
        if v:
            status = {"resolved": "RESOLVED", "partially_resolved": "PARTLY RESOLVED", "unresolved": "NOT RESOLVED"}.get(v.get("status"), str(v.get("status")).upper())
            lines.append(f"*Verification: {status}.* {v.get('passed')} of {v.get('total')} checks passed.")
            for d in (v.get("details") or "").splitlines()[:5]:
                m = re.match(r"- \[(PASS|FAIL)\] (\S+) (\{.*?\}) expect=", d)
                if m:
                    try:
                        args = json.loads(m.group(3).replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null"))
                    except json.JSONDecodeError:
                        args = {}
                    lines.append(f"• {m.group(1)}: {describe_tool_call(m.group(2), args)}")
    return {"text": "\n".join(lines)[:MAX_SECTION]}


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
        sev = (report.get("severity") or {}).get("level") or (rec.get("justification") or {}).get("severity") or "P1"
        steps = steps_from_audit(self.audit, iid)
        existing = self.audit.get_meta(iid, "slack_thread")
        payload = plan_message(iid, report, rec, steps, self._mention(sev), self._approvers(sev), in_thread=bool(existing))
        if existing:
            ch, ts = self._send(existing["channel"], payload, existing["ts"])
        else:
            ch, ts, _ = self._thread(iid, sev, payload)
        self.audit.set_meta(iid, f"slack_msg:{rec['id']}", {"channel": ch, "ts": ts, "text": payload["text"], "blocks": payload["blocks"]})

    def post_report(self, iid: str, report: dict, needs_human: bool) -> None:
        sev = (report.get("severity") or {}).get("level", "P1")
        steps = steps_from_audit(self.audit, iid)
        existing = self.audit.get_meta(iid, "slack_thread")
        payload = report_message(iid, report, steps, needs_human, self._mention(sev), in_thread=bool(existing))
        if existing:
            self._send(existing["channel"], payload, existing["ts"])
        else:
            self._thread(iid, sev, payload)

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
