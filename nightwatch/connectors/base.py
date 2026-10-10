"""Shared tool interface. Every connector registers typed tools and typed actions here.

A tool is a read-only (risk 0) or bounded diagnostic (risk 1) operation. The model may call tools.
An action changes infrastructure (risk 2+). The model can only *propose* an action. The action worker
executes it after the policy service validates an approval that is bound to the exact parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from ..runner import CmdResult


class RiskLevel(IntEnum):
    READ = 0          # read-only investigation
    DIAGNOSTIC = 1    # bounded diagnostic test (DNS lookup, TCP connect, HTTP GET)
    CHANGE = 2        # approved operational change
    HIGH_IMPACT = 3   # high-impact or destructive; blocked in v1


class Args(BaseModel):
    """Base class for tool and action inputs. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolOutput:
    ok: bool
    text: str
    error_code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cmd(cls, res: CmdResult, prefix: str = "") -> "ToolOutput":
        code = res.error_code or (None if res.exit_code == 0 else "NONZERO_EXIT")
        return cls(ok=res.ok, text=(prefix + res.as_text()) if prefix else res.as_text(), error_code=code)


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[Args]
    risk: RiskLevel
    connector: str
    fn: Callable[[Any], ToolOutput]
    timeout_s: float = 15
    max_output_bytes: int = 12000
    targets: Callable[[Any], list[str]] = lambda a: []

    def schema(self) -> dict[str, Any]:
        s = self.input_model.model_json_schema()
        s.pop("title", None)
        s.setdefault("additionalProperties", False)
        return {"name": self.name, "description": self.description, "input_schema": s}


@dataclass
class ActionResult:
    ok: bool
    detail: str
    error_code: str | None = None


@dataclass
class Action:
    name: str
    description: str
    params_model: type[Args]
    risk: RiskLevel
    connector: str
    targets: Callable[[Any], list[str]]
    fingerprint: Callable[[Any], str]  # current state of the target; approval is bound to it
    execute: Callable[[Any], ActionResult]
    rollback: str = ""


TOOLS: dict[str, Tool] = {}
ACTIONS: dict[str, Action] = {}


def tool(name: str, description: str, input_model: type[Args], risk: RiskLevel, connector: str,
         timeout_s: float = 15, max_output_bytes: int = 12000,
         targets: Callable[[Any], list[str]] | None = None):
    if risk > RiskLevel.DIAGNOSTIC:
        raise ValueError(f"tool {name}: tools must be risk 0 or 1; use @action for changes")

    def deco(fn: Callable[[Any], ToolOutput]):
        TOOLS[name] = Tool(name, description, input_model, risk, connector, fn, timeout_s,
                           max_output_bytes, targets or (lambda a: []))
        return fn
    return deco


def action(name: str, description: str, params_model: type[Args], risk: RiskLevel, connector: str,
           targets: Callable[[Any], list[str]], fingerprint: Callable[[Any], str], rollback: str = ""):
    def deco(fn: Callable[[Any], ActionResult]):
        ACTIONS[name] = Action(name, description, params_model, risk, connector, targets, fingerprint, fn, rollback)
        return fn
    return deco
