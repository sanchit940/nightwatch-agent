"""Importing this package registers every connector's tools and actions."""

import os

from . import aws, ci, docker, grafana, kubernetes, linux, monitoring, neatlogs_api, network  # noqa: F401
from .base import ACTIONS, TOOLS, RiskLevel  # noqa: F401

# A connector without configuration is hidden from the model: no wasted calls, smaller prompt.
_REQUIRES = {
    "aws": ("NIGHTWATCH_AWS_PROFILE", "NIGHTWATCH_AWS_CREDENTIALS"),
    "neatlogs": ("NEATLOGS_READ_TOKEN", "NEATLOGS_PUBLIC_API_KEY"),
    "grafana": ("GRAFANA_URL",),
    "loki": ("LOKI_URL", "GRAFANA_URL"),
}


def enabled_tools() -> dict:
    """Tools whose connector is configured (any one of the listed environment variables is set)."""
    return {n: t for n, t in TOOLS.items()
            if t.connector not in _REQUIRES or any(os.environ.get(v) for v in _REQUIRES[t.connector])}
