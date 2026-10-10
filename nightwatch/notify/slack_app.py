"""Slack app (Socket Mode): Approve / Reject / Suggest another fix / Give instructions.

Each click is checked against policy.yaml (slack.approvers per severity). Decisions go to the same audit database
that the incident runner polls, through the same policy service. The Slack app never executes a change itself.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..audit import Audit
from ..policy import Policy
from .slack import SlackNotifier, describe_action


def _utc() -> str:
    return time.strftime("%H:%M UTC", time.gmtime())


def build_app(bot_token: str, make: Callable[[], tuple[Audit, Policy]], slack_cfg: dict) -> App:
    app = App(token=bot_token)

    def ctx(client):
        audit, policy = make()
        return audit, policy, SlackNotifier(slack_cfg, audit, client=client)

    def deny(respond, text: str) -> None:
        respond(response_type="ephemeral", replace_original=False, text=text)

    @app.action("nw_approve")
    def on_approve(ack, body, client, respond):
        ack()
        audit, policy, notifier = ctx(client)
        aid, user = body["actions"][0]["value"], body["user"]["id"]
        rec = audit.approval(aid)
        if rec is None:
            return deny(respond, f"Approval {aid} does not exist.")
        sev = (rec.get("justification") or {}).get("severity")
        if not policy.can_decide(user, sev):
            return deny(respond, f"You are not an approver for {sev} incidents. No change was made.")
        d = policy.approve(aid, f"slack:{user}")
        iid = rec["incident_id"]
        if d.allowed:
            notifier.close_buttons(iid, aid, f"✅ Approved by <@{user}> at {_utc()}. NightWatch runs this exact change now.")
            notifier.post_update(iid, f"✅ <@{user}> approved: {describe_action(rec['action'], rec['params'])}")
        else:
            notifier.close_buttons(iid, aid, f"⛔ Not approved: `{d.code}`. {d.reason}")
            deny(respond, f"Not approved: {d.code}. {d.reason}")

    @app.action("nw_reject")
    def on_reject(ack, body, client, respond):
        ack()
        audit, policy, notifier = ctx(client)
        aid, user = body["actions"][0]["value"], body["user"]["id"]
        rec = audit.approval(aid)
        if rec is None:
            return deny(respond, f"Approval {aid} does not exist.")
        sev = (rec.get("justification") or {}).get("severity")
        if not policy.can_decide(user, sev):
            return deny(respond, f"You are not an approver for {sev} incidents.")
        d = policy.reject(aid, f"slack:{user}", "rejected in Slack")
        if d.allowed:
            notifier.close_buttons(rec["incident_id"], aid, f"❌ Rejected by <@{user}> at {_utc()}. No change was made.")
            notifier.post_update(rec["incident_id"], f"❌ <@{user}> rejected the plan. No change was made. "
                                                     "Use *Give instructions* or fix it by hand.")
        else:
            deny(respond, f"Cannot reject: {d.code}. {d.reason}")

    def open_instruction_modal(body, client, respond, title: str):
        meta = json.loads(body["actions"][0]["value"])
        audit, policy, _ = ctx(client)
        sev = (audit.get_meta(meta["iid"], "slack_thread") or {}).get("severity")
        if not policy.can_decide(body["user"]["id"], sev):
            return deny(respond, f"You are not an approver for {sev} incidents.")
        client.views_open(trigger_id=body["trigger_id"], view={
            "type": "modal", "callback_id": "nw_guidance", "private_metadata": json.dumps(meta),
            "title": {"type": "plain_text", "text": title[:24]},
            "submit": {"type": "plain_text", "text": "Send"}, "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    "Write one instruction. Use short sentences.\n"
                    "• NightWatch tests your instruction first.\n"
                    "• A change still needs your approval.\n"
                    "• If you fixed it by hand, say what you did. NightWatch then verifies it."}},
                {"type": "input", "block_id": "g", "label": {"type": "plain_text", "text": "Instruction"},
                 "element": {"type": "plain_text_input", "action_id": "text", "multiline": True, "max_length": 1500,
                             "placeholder": {"type": "plain_text", "text": "Example: Do not restart. Roll back deployment shop/api to revision 4."}}},
            ]})

    @app.action("nw_suggest")
    def on_suggest(ack, body, client, respond):
        ack()
        open_instruction_modal(body, client, respond, "Suggest another fix")

    @app.action("nw_instruct")
    def on_instruct(ack, body, client, respond):
        ack()
        open_instruction_modal(body, client, respond, "Give instructions")

    @app.view("nw_guidance")
    def on_guidance(ack, body, client, view):
        ack()
        audit, policy, notifier = ctx(client)
        meta = json.loads(view["private_metadata"])
        user = body["user"]["id"]
        text = view["state"]["values"]["g"]["text"]["value"].strip()
        iid, aid = meta["iid"], meta.get("aid")
        audit.add_guidance(iid, f"slack:{user}", text, aid)
        if aid and (audit.approval(aid) or {}).get("status") == "pending":
            policy.reject(aid, f"slack:{user}", "replaced by an approver instruction")
            notifier.close_buttons(iid, aid, f"✍️ <@{user}> gave another instruction at {_utc()}. This plan was not executed.")
        quoted = "\n".join(f"> {l}" for l in text.splitlines())
        notifier.post_update(iid, f"✍️ Instruction from <@{user}>:\n{quoted}\nNightWatch tests it now and posts the result here.")

    return app


def start(bot_token: str, app_token: str, audit_db: Path, policy_file: Path, secret: str, slack_cfg: dict) -> SocketModeHandler:
    """Connect in the background (non-blocking). Each event uses its own database connection."""
    app = build_app(bot_token, lambda: (lambda a: (a, Policy(policy_file, a, secret)))(Audit(audit_db)), slack_cfg)
    handler = SocketModeHandler(app, app_token)
    handler.connect()
    return handler
