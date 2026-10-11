"""Runtime settings. Values come from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines into os.environ. Existing variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


@dataclass
class Settings:
    model: str = "claude-sonnet-5-5"   # direct Anthropic API; the neatHack proxy uses claude-haiku
    effort: str = "high"
    max_tokens: int = 32000
    policy_file: Path = ROOT / "policy.yaml"
    audit_db: Path = ROOT / "runs" / "audit.db"
    knowledge_dir: Path = ROOT / "knowledge"
    approval_secret: str = ""
    prometheus_url: str = "http://localhost:9090"
    neatlogs_api_key: str = ""
    prompt_version: str = "v2"
    llm_base_url: str = ""      # e.g. the neatHack LiteLLM proxy; empty = api.anthropic.com
    llm_api_key: str = ""
    slack_bot_token: str = ""   # xoxb-...  (empty = dry run: messages go to runs/slack-outbox.jsonl)
    slack_app_token: str = ""   # xapp-...  (Socket Mode, for buttons and the instruction form)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        s = cls()
        s.model = os.environ.get("NIGHTWATCH_MODEL", s.model)
        s.effort = os.environ.get("NIGHTWATCH_EFFORT", s.effort)
        s.approval_secret = os.environ.get("NIGHTWATCH_APPROVAL_SECRET", "")
        s.prometheus_url = os.environ.get("PROMETHEUS_URL", s.prometheus_url)
        s.neatlogs_api_key = os.environ.get("NEATLOGS_API_KEY", "")
        s.prompt_version = os.environ.get("NIGHTWATCH_PROMPT", s.prompt_version)
        # Own variable names: a parent process (for example Claude Code) can set ANTHROPIC_BASE_URL.
        s.llm_base_url = os.environ.get("NIGHTWATCH_LLM_BASE_URL", "")
        s.llm_api_key = os.environ.get("LITELLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
        s.slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        s.slack_app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if db := os.environ.get("NIGHTWATCH_AUDIT_DB"):
            s.audit_db = Path(db)
        s.audit_db.parent.mkdir(parents=True, exist_ok=True)
        return s


def llm_spend(s: Settings) -> float | None:
    """Spend reported by a LiteLLM proxy for this key (USD), or None when not available."""
    if not s.llm_base_url:
        return None
    import json
    import urllib.request
    try:
        req = urllib.request.Request(s.llm_base_url.rstrip("/") + "/key/info", headers={"Authorization": f"Bearer {s.llm_api_key}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return float(json.loads(r.read())["info"]["spend"])
    except Exception:  # noqa: BLE001
        return None
