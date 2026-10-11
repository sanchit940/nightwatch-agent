"""Stable pseudonymization of cloud identifiers before tool output reaches the LLM, the traces and Slack.

Masked: 12-digit account IDs (also inside ARNs), public IPv4 addresses and CIDRs, IAM user names, assumed-role
session names, e-mail addresses. Kept: private IPs (RFC 1918), 0.0.0.0/0, resource IDs (i-..., sg-...), role names:
they are needed for network reasoning and do not identify a person or the account.

The mapping is stable for the life of the process, and reversible: when the model passes a placeholder back into a
tool, unmask() restores the real value before the cloud API call.
"""

from __future__ import annotations

import ipaddress
import re
import threading

_ARN = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:(\d{12})?:[^\s\"',\]\)]+")
_ACCOUNT = re.compile(r"(?<![\d.])\d{12}(?![\d.])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(/\d{1,2})?(?![\d.])")
_USER_FIELD = re.compile(r'("(?:user|userName|username|Username)"\s*:\s*")([^"]+)(")')


class Masker:
    def __init__(self) -> None:
        self._fwd: dict[tuple[str, str], str] = {}
        self._rev: dict[str, str] = {}
        self._n: dict[str, int] = {}
        self._lock = threading.Lock()

    def _ph(self, kind: str, real: str) -> str:
        with self._lock:
            if (kind, real) in self._fwd:
                return self._fwd[(kind, real)]
            n = self._n[kind] = self._n.get(kind, 0) + 1
            ph = {"account": f"{n:012d}",
                  "ip": f"203.0.113.{n}" if n < 255 else f"198.51.100.{n % 254 + 1}",
                  "user": f"user-{n}",
                  "email": f"email-{n}@masked.invalid"}[kind]
            self._fwd[(kind, real)] = ph
            self._rev[ph] = real
            return ph

    # ---------- forward ----------
    def _mask_name(self, name: str) -> str:
        if _EMAIL.fullmatch(name):
            return self._ph("email", name)
        return self._ph("user", name)

    def _mask_arn(self, m: re.Match) -> str:
        arn = m.group(0)
        if m.group(1):
            arn = arn.replace(f":{m.group(1)}:", f":{self._ph('account', m.group(1))}:", 1)
        # user/NAME and assumed-role/ROLE/SESSION identify people; role/NAME is kept.
        arn = re.sub(r"(:user/(?:[^/\s]+/)*)([^/\s\"',]+)$", lambda x: x.group(1) + self._mask_name(x.group(2)), arn)
        arn = re.sub(r"(:assumed-role/[^/\s]+/)([^/\s\"',]+)$", lambda x: x.group(1) + self._mask_name(x.group(2)), arn)
        return arn

    def _mask_ip(self, m: re.Match) -> str:
        ip, prefix = m.group(1), m.group(2) or ""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return m.group(0)
        if addr.is_private or addr.is_unspecified or addr.is_loopback or addr.is_link_local or ip.startswith(("203.0.113.", "198.51.100.")):
            return m.group(0)
        return self._ph("ip", ip) + prefix

    def mask(self, text: str) -> str:
        if not text:
            return text
        text = _ARN.sub(self._mask_arn, text)
        text = _USER_FIELD.sub(lambda m: m.group(1) + self._mask_name(m.group(2)) + m.group(3), text)
        text = _EMAIL.sub(lambda m: self._ph("email", m.group(0)) if not m.group(0).endswith("@masked.invalid") else m.group(0), text)
        text = _ACCOUNT.sub(lambda m: m.group(0) if m.group(0) in self._rev else self._ph("account", m.group(0)), text)
        text = _IPV4.sub(self._mask_ip, text)
        return text

    # ---------- reverse ----------
    def unmask(self, value):
        if isinstance(value, str):
            if value in self._rev:
                return self._rev[value]
            # Placeholders inside a longer string (for example an ARN with a masked account).
            for ph in sorted(self._rev, key=len, reverse=True):
                if ph in value:
                    value = value.replace(ph, self._rev[ph])
            return value
        if isinstance(value, list):
            return [self.unmask(v) for v in value]
        if isinstance(value, dict):
            return {k: self.unmask(v) for k, v in value.items()}
        return value


MASKER = Masker()
