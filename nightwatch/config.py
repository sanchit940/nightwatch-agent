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
    model: str = "claude-opus-4-8"
    effort: str = "high"
    max_tokens: int = 32000
    policy_file: Path = ROOT / "policy.yaml"
    audit_db: Path = ROOT / "runs" / "audit.db"
    knowledge_dir: Path = ROOT / "knowledge"
    approval_secret: str = ""
    prometheus_url: str = "http://localhost:9090"
    neatlogs_api_key: str = ""
    prompt_version: str = "v2"
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
        if db := os.environ.get("NIGHTWATCH_AUDIT_DB"):
            s.audit_db = Path(db)
        s.audit_db.parent.mkdir(parents=True, exist_ok=True)
        return s
