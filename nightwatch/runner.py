"""Bounded command execution. No shell. Fixed argv only. Output is limited and redacted."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass

# Patterns for values that must never reach the model, the trace or the audit log.
_REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<redacted:private-key>"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "<redacted:aws-key-id>"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/]+=*"), r"\1<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "<redacted:api-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<redacted:github-token>"),
    (
        re.compile(r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*)\s*([=:])\s*(\"[^\"]*\"|'[^']*'|[^\s,;}\"']+)"),
        r"\1\2<redacted>",
    ),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+@"), r"\1<redacted>@"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


@dataclass
class CmdResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    error_code: str | None = None  # TIMEOUT, NOT_FOUND, EXEC_ERROR

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error_code is None

    def as_text(self) -> str:
        parts = [f"$ {' '.join(self.argv)}", f"exit={self.exit_code} duration_ms={self.duration_ms}"]
        if self.error_code:
            parts.append(f"error_code={self.error_code}")
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.truncated:
            parts.append("[output truncated]")
        return "\n".join(parts)


def _limit(text: str, max_bytes: int) -> tuple[str, bool]:
    data = text.encode(errors="replace")
    if len(data) <= max_bytes:
        return text, False
    # Keep head and tail: errors are often at the end of the output.
    half = max_bytes // 2
    return data[:half].decode(errors="replace") + "\n...\n" + data[-half:].decode(errors="replace"), True


def run(argv: list[str], timeout: float = 15.0, max_bytes: int = 12000, stdin: str | None = None) -> CmdResult:
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("argv must be a non-empty list of strings")
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=stdin, shell=False)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return CmdResult(argv, -1, redact(out)[:max_bytes], "", int((time.monotonic() - start) * 1000), error_code="TIMEOUT")
    except FileNotFoundError:
        return CmdResult(argv, -1, "", f"executable not found: {argv[0]}", 0, error_code="NOT_FOUND")
    except OSError as e:
        return CmdResult(argv, -1, "", str(e), 0, error_code="EXEC_ERROR")
    stdout, t1 = _limit(redact(proc.stdout), max_bytes)
    stderr, t2 = _limit(redact(proc.stderr), max_bytes // 4)
    return CmdResult(argv, proc.returncode, stdout, stderr, int((time.monotonic() - start) * 1000), t1 or t2)
