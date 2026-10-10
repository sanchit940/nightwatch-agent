"""NightWatch command line.

  nightwatch investigate "Users get 502 on http://localhost:8080"   # one incident, interactive approval
  nightwatch watch                                                  # unattended: detect -> investigate -> wait for approval
  nightwatch approvals | approve <id> | reject <id>                 # human approval from another terminal
  nightwatch audit [--incident ID] [--verify]                       # audit trail and hash-chain check
  nightwatch tools                                                  # tool catalog with risk levels
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .audit import Audit
from .config import Settings
from .connectors import ACTIONS, TOOLS
from .policy import Policy

console = Console()


def init_tracing(s: Settings, workflow: str) -> None:
    if not s.neatlogs_api_key:
        console.print("[yellow]NEATLOGS_API_KEY is not set: traces are not exported.[/]")
        return
    import neatlogs
    neatlogs.init(api_key=s.neatlogs_api_key, workflow_name=workflow,
                  tags=[f"prompt:{s.prompt_version}", f"model:{s.model}", *s.tags])


def shutdown_tracing(s: Settings) -> None:
    if s.neatlogs_api_key:
        import neatlogs
        neatlogs.flush()
        neatlogs.shutdown()


def render_event(kind: str, d: dict) -> None:
    if kind == "incident":
        console.rule(f"[bold]Incident {d['id']}")
        console.print(d["symptom"])
    elif kind == "thought":
        console.print(f"[dim italic]{d['text'].strip()[:600]}[/]")
    elif kind == "tool_call":
        mark = "[green]✓[/]" if d["ok"] else f"[red]✗ {d.get('error_code') or 'FAILED'}[/]"
        console.print(f"{mark} [cyan]{d['tool']}[/] {json.dumps(d['args'])[:140]} [dim]{d['ms']} ms[/]")
    elif kind == "tool_denied":
        console.print(f"[red]⛔ policy denied {d['tool']}: {d['code']} {d['reason'][:200]}[/]")
    elif kind == "hypotheses":
        t = Table("id", "status", "cause", "next test", title="Hypotheses", show_lines=False)
        for h in d.get("hypotheses", []):
            color = {"supported": "green", "rejected": "red"}.get(h.get("status"), "yellow")
            t.add_row(h.get("id", ""), f"[{color}]{h.get('status')}[/]", h.get("cause", "")[:80], (h.get("next_test") or "")[:60])
        console.print(t)
    elif kind == "proposal":
        console.print(Panel(f"[bold]{d.get('action')}[/] {json.dumps(d.get('params'))}\nreason: {d.get('reason')}\n"
                            f"impact: {d.get('expected_impact')}\nrisk: {d.get('risk')}\nrollback: {d.get('rollback')}\n"
                            f"policy: [bold]{d['decision']}[/] {d['detail']}", title="Proposed change", border_style="magenta"))
    elif kind == "approval":
        console.print(f"[magenta]approval {d['id']} -> {d['status']}[/]")
    elif kind == "execution":
        v = d.get("verification") or {}
        color = "green" if d["ok"] else "red"
        console.print(Panel(f"executed={d['executed']} ok={d['ok']} code={d['code']}\n{d['detail'][:600]}\n\n"
                            f"verification: [bold]{v.get('status')}[/] {v.get('passed')}/{v.get('total')}\n{v.get('details', '')[:800]}",
                            title=f"Execution {d['approval_id']}", border_style=color))
    elif kind == "report":
        rc = d.get("root_cause", {})
        body = (f"[bold]{d.get('summary')}[/]\n\nroot cause: [bold]{rc.get('category')}[/] on {rc.get('resource')} "
                f"(confidence {rc.get('confidence')})\n{rc.get('description')}\n")
        if rc.get("symptoms_not_causes"):
            body += "symptoms (not causes): " + "; ".join(rc["symptoms_not_causes"]) + "\n"
        body += "\nevidence:\n" + "\n".join(f"- {e.get('tool')}: {e.get('finding')}" for e in d.get("evidence", [])[:10])
        if d.get("known_unknowns"):
            body += "\n\nknown unknowns:\n" + "\n".join(f"- {k}" for k in d["known_unknowns"])
        body += "\n\nrecommendations:\n" + "\n".join(f"- [{r.get('type')}] {r.get('description')} {r.get('approval_id') or ''}"
                                                     for r in d.get("recommendations", []))
        if d.get("resolution"):
            body += f"\n\nresolution: [bold]{d['resolution']}[/]"
        console.print(Panel(body, title="Investigation report", border_style="blue"))


def interactive_prompt(rec: dict) -> bool:
    j = rec["justification"]
    console.print(Panel(f"[bold]{rec['action']}[/] {json.dumps(rec['params'])}\ntargets: {rec['targets']}\n"
                        f"bound state: {rec['fingerprint']}\nexpires: {time.strftime('%H:%M:%S', time.localtime(rec['expires_at']))}\n"
                        f"reason: {j.get('reason')}\nrollback: {j.get('rollback')}", title=f"Approve {rec['id']}?", border_style="yellow"))
    return console.input("[bold yellow]Approve this exact change? [y/N] [/]").strip().lower() in ("y", "yes")


def build_slack(s: Settings, audit: Audit, policy: Policy):
    """Notifier (real or dry run) and, with an app token, the Socket Mode listener for buttons."""
    from .notify.slack import SlackNotifier
    cfg = policy.cfg.get("slack") or {}
    if not s.approval_secret:
        raise SystemExit("Set NIGHTWATCH_APPROVAL_SECRET: the Slack app and the runner must sign approvals with the same secret.")
    if not s.slack_bot_token:
        console.print("[yellow]SLACK_BOT_TOKEN is not set: Slack dry run. Messages go to runs/slack-outbox.jsonl. "
                      "Decide with: nightwatch approve|reject <id>, nightwatch guide <incident> \"text\".[/]")
        return SlackNotifier(cfg, audit, client=None, console=console), None
    from slack_sdk import WebClient
    notifier = SlackNotifier(cfg, audit, client=WebClient(token=s.slack_bot_token))
    handler = None
    if s.slack_app_token:
        from .notify import slack_app
        handler = slack_app.start(s.slack_bot_token, s.slack_app_token, s.audit_db, s.policy_file, s.approval_secret, cfg)
        console.print("[green]Slack app connected (Socket Mode): buttons are live.[/]")
    else:
        console.print("[yellow]SLACK_APP_TOKEN is not set: messages are posted, but buttons do not work. Use the CLI to decide.[/]")
    return notifier, handler


def build(s: Settings):
    audit = Audit(s.audit_db)
    policy = Policy(s.policy_file, audit, s.approval_secret)
    return audit, policy


def cmd_investigate(a, s: Settings) -> int:
    from .incident import IncidentRunner, make_approver
    audit, policy = build(s)
    init_tracing(s, "nightwatch-investigate")
    notifier = None
    if a.approval == "slack":
        notifier, _ = build_slack(s, audit, policy)
    approver = make_approver("wait" if a.approval == "slack" else a.approval, interactive_prompt)
    wait = {"interactive": 900, "wait": 900, "slack": 3600}.get(a.approval, 0)
    runner = IncidentRunner(s, audit, policy, approver, render_event, approval_wait_s=wait,
                            approver_name="human:cli" if a.approval == "interactive" else a.approval,
                            notifier=notifier, guidance_wait_s=1800 if a.approval == "slack" else 0)
    from .config import llm_spend
    spend0 = llm_spend(s)
    try:
        out = runner.handle(a.symptom, source="human", max_rounds=5 if a.approval == "slack" else 3)
    finally:
        shutdown_tracing(s)
    spend1 = llm_spend(s)
    if spend0 is not None and spend1 is not None:
        console.print(f"measured LLM spend for this incident: ${spend1 - spend0:.4f} (key total ${spend1:.2f})")
    st = out.stats
    console.print(f"\n[bold]{out.incident_id}[/] status={out.status} rounds={out.rounds} tool_calls={st.tool_calls} "
                  f"errors={st.tool_errors} denials={st.policy_denials} llm_calls={st.llm_calls} time={st.seconds}s "
                  f"tokens in/out={st.input_tokens}/{st.output_tokens} cache_read={st.cache_read_tokens} cost≈${st.cost_usd(s.model)}")
    return 0


def cmd_watch(a, s: Settings) -> int:
    from .incident import IncidentRunner, make_approver
    from .watch import Finding, run_watch
    audit, policy = build(s)
    init_tracing(s, "nightwatch-watch")
    notifier = None
    if a.approval == "slack":
        notifier, _ = build_slack(s, audit, policy)
    runner = IncidentRunner(s, audit, policy, make_approver("wait" if a.approval == "slack" else a.approval), render_event,
                            approval_wait_s={"wait": 900, "slack": 3600}.get(a.approval, 0), approver_name=a.approval,
                            notifier=notifier, guidance_wait_s=1800 if a.approval == "slack" else 0)

    def handle(f: Finding) -> None:
        if audit.find_open_incident(f.key):
            console.print(f"[dim]already handling {f.key}; no new alert[/]")
            return
        out = runner.handle(f.symptom, source="watch", dedup_key=f.key, max_rounds=5 if a.approval == "slack" else 3)
        console.print(f"[bold]{out.incident_id}[/] -> {out.status}")

    def tick(findings) -> None:
        stamp = time.strftime("%H:%M:%S")
        console.print(f"[dim]{stamp} watch: {'healthy' if not findings else ', '.join(f.key for f in findings)}[/]")

    console.print(f"[bold]NightWatch is watching[/] (interval {a.interval}s, approval mode {a.approval}). Ctrl-C to stop.")
    try:
        run_watch(handle, interval_s=a.interval, confirm_rounds=a.confirm, on_tick=tick, once=a.once)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_tracing(s)
    return 0


def cmd_check(a, s: Settings) -> int:
    """One round of the watch checks. Deterministic; no model call; no API key needed."""
    from .watch import group, load_checks, scan
    findings = scan(load_checks())
    if not findings:
        console.print("[green]healthy: all watch checks passed[/]")
        return 0
    console.print(Panel(group(findings)[0].symptom, title="Findings (an incident opens after 2 failed rounds in watch mode)", border_style="red"))
    return 1


def cmd_approvals(a, s: Settings) -> int:
    audit, _ = build(s)
    t = Table("id", "incident", "action", "params", "status", "expires")
    for r in audit.approvals(status=None if a.all else "pending"):
        t.add_row(r["id"], r["incident_id"] or "", r["action"], json.dumps(r["params"]), r["status"],
                  time.strftime("%H:%M:%S", time.localtime(r["expires_at"])))
    console.print(t)
    return 0


def cmd_approve(a, s: Settings, approve: bool) -> int:
    audit, policy = build(s)
    rec = audit.approval(a.id)
    if rec is None:
        console.print(f"[red]no approval {a.id}[/]")
        return 1
    console.print_json(json.dumps({k: rec[k] for k in ("action", "params", "targets", "fingerprint")} | {"reason": rec["justification"].get("reason")}))
    d = policy.approve(a.id, f"human:{a.by}") if approve else policy.reject(a.id, f"human:{a.by}", a.reason or "")
    console.print(f"{d.code}: {d.reason}")
    return 0 if d.allowed else 1


def cmd_guide(a, s: Settings) -> int:
    """Give the agent an instruction instead of (or after) its plan. Same path as the Slack form."""
    audit, policy = build(s)
    if not audit.incident(a.incident):
        console.print(f"[red]no incident {a.incident}[/]")
        return 1
    pending = [r for r in audit.approvals(status="pending", incident_id=a.incident)]
    aid = a.approval or (pending[-1]["id"] if pending else None)
    audit.add_guidance(a.incident, f"human:{a.by}", a.text, aid)
    if aid and (audit.approval(aid) or {}).get("status") == "pending":
        policy.reject(aid, f"human:{a.by}", "replaced by an approver instruction")
    console.print(f"instruction stored for {a.incident}" + (f"; approval {aid} replaced (not executed)" if aid else ""))
    return 0


def cmd_slack_test(a, s: Settings) -> int:
    """Send (or preview) a sample plan message, to check tokens, channels and the layout."""
    import tempfile
    from .notify.slack import SlackNotifier
    audit = Audit(tempfile.mktemp(suffix=".db"))
    policy = Policy(s.policy_file, audit, s.approval_secret or "test")
    iid = audit.open_incident("Sample: users get 503 errors on http://localhost:8080", "slack-test")
    audit.log("tool_call", {"tool": "net_http_check", "args": {"url": "http://localhost:8080/", "source": "agent"}, "ok": False, "error_code": "HTTP_503"}, iid)
    audit.log("tool_call", {"tool": "docker_inspect", "args": {"container": "nw-redis"}, "ok": True}, iid)
    report = {"summary": "This is a TEST message. Redis is stopped. The API returns 503.",
              "severity": {"level": a.severity, "reason": "All requests fail (test)."},
              "problem_statement": {"symptom": "HTTP 503 on every request.", "affected_service": "shop web and API", "start_time": "now"},
              "evidence": [{"tool": "docker_inspect", "finding": "nw-redis is exited with code 0."}],
              "root_cause": {"category": "dependency_down", "resource": "nw-redis", "description": "Redis stopped. The API cannot reach it.",
                             "confidence": "high", "symptoms_not_causes": ["HTTP 503 on the web entry point."]},
              "known_unknowns": ["Who stopped Redis."], "recommendations": []}
    rec = {"id": "apr-test0000", "action": "start_container", "params": {"container": "nw-redis"}, "fingerprint": "test",
           "expires_at": time.time() + 900, "justification": {"severity": a.severity, "expected_effect": "removes_root_cause",
           "reason": "Redis is stopped.", "risk": "Low. Same image and config.", "rollback": "docker stop nw-redis.",
           "verification": [{"tool": "net_http_check", "args": {"url": "http://localhost:8080/"}, "expect": {"ok": True}}]}}
    client = None
    if s.slack_bot_token:
        from slack_sdk import WebClient
        client = WebClient(token=s.slack_bot_token)
    SlackNotifier(policy.cfg.get("slack") or {}, audit, client=client, console=console).post_plan(iid, report, rec)
    console.print("[green]sent[/]" if client else "[yellow]dry run (no SLACK_BOT_TOKEN)[/]")
    return 0


def cmd_audit(a, s: Settings) -> int:
    audit, _ = build(s)
    if a.verify:
        ok, seq = audit.verify_chain()
        console.print("[green]audit chain intact[/]" if ok else f"[red]audit chain BROKEN at event {seq}[/]")
        return 0 if ok else 2
    if not a.incident:
        t = Table("incident", "opened", "status", "symptom")
        for i in audit.incidents():
            t.add_row(i["id"], time.strftime("%m-%d %H:%M:%S", time.localtime(i["opened_at"])), i["status"], i["symptom"][:80])
        console.print(t)
        return 0
    for e in audit.events(a.incident):
        p = e["payload"]
        brief = json.dumps({k: v for k, v in p.items() if k != "output"})[:220]
        console.print(f"[dim]{e['seq']:>5} {time.strftime('%H:%M:%S', time.localtime(e['ts']))}[/] [cyan]{e['kind']:<18}[/] {brief}")
    return 0


def cmd_tools(a, s: Settings) -> int:
    t = Table("name", "connector", "risk", "kind", "description")
    for x in sorted(TOOLS.values(), key=lambda x: (x.connector, x.name)):
        t.add_row(x.name, x.connector, str(int(x.risk)), "tool", x.description[:90])
    for x in sorted(ACTIONS.values(), key=lambda x: x.name):
        t.add_row(x.name, x.connector, str(int(x.risk)), "action" + (" [red](blocked)[/]" if x.risk >= 3 else ""), x.description[:90])
    console.print(t)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nightwatch")
    p.add_argument("--prompt", choices=["v1", "v2", "v3", "v4", "v5"], help="prompt version (default from env, v2)")
    p.add_argument("--model", help="override NIGHTWATCH_MODEL")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("investigate")
    i.add_argument("symptom")
    i.add_argument("--approval", default="interactive", choices=["interactive", "wait", "slack", "policy-only", "deny-all"])
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=20)
    w.add_argument("--confirm", type=int, default=2, help="consecutive failed rounds before an incident opens")
    w.add_argument("--approval", default="wait", choices=["wait", "slack", "policy-only"])
    w.add_argument("--once", action="store_true")
    sub.add_parser("check", help="run the watch checks once; no model call")
    ap = sub.add_parser("approvals")
    ap.add_argument("--all", action="store_true")
    for name in ("approve", "reject"):
        x = sub.add_parser(name)
        x.add_argument("id")
        x.add_argument("--by", default="oncall")
        x.add_argument("--reason")
    gd = sub.add_parser("guide", help="give the agent an instruction for an incident (replaces a pending plan)")
    gd.add_argument("incident")
    gd.add_argument("text")
    gd.add_argument("--approval", help="approval id to replace (default: the latest pending one)")
    gd.add_argument("--by", default="oncall")
    st = sub.add_parser("slack-test", help="send or preview a sample Slack plan message")
    st.add_argument("--severity", default="P1", choices=["P0", "P1", "P2"])
    au = sub.add_parser("audit")
    au.add_argument("--incident")
    au.add_argument("--verify", action="store_true")
    sub.add_parser("tools")
    a = p.parse_args(argv)
    s = Settings.from_env()
    if a.prompt:
        s.prompt_version = a.prompt
    if a.model:
        s.model = a.model
    return {
        "investigate": lambda: cmd_investigate(a, s),
        "watch": lambda: cmd_watch(a, s),
        "check": lambda: cmd_check(a, s),
        "approvals": lambda: cmd_approvals(a, s),
        "approve": lambda: cmd_approve(a, s, True),
        "reject": lambda: cmd_approve(a, s, False),
        "audit": lambda: cmd_audit(a, s),
        "guide": lambda: cmd_guide(a, s),
        "slack-test": lambda: cmd_slack_test(a, s),
        "tools": lambda: cmd_tools(a, s),
    }[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
