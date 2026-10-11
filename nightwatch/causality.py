"""Causal-claim guard: "time correlation is not causation", enforced in code.

A causal link (cause -> effect) is accepted only when
  1. the evidence quote exists in a real tool result of this incident, and
  2. the two parts are connected in the trusted dependency graph (inventory.yaml), OR the quoted line itself names both.
Two events that only happened at about the same time do not pass.
"""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from .config import ROOT

STOP = {"the", "and", "for", "with", "from", "that", "this", "container", "service", "deployment", "pod", "pods", "host",
        "server", "was", "were", "not", "has", "have", "cannot", "into", "because", "after", "before", "down", "error"}


@lru_cache(maxsize=1)
def graph() -> tuple[dict[str, str], dict[str, set[str]]]:
    """(alias -> node, node -> neighbours). Built only from the human-owned inventory."""
    inv = yaml.safe_load((ROOT / "inventory.yaml").read_text()) or {}
    alias: dict[str, str] = {}
    adj: dict[str, set[str]] = {}

    def node(n: str, *names: str) -> str:
        adj.setdefault(n, set())
        for x in (n, *names):
            if x:
                alias[x.lower()] = n
        return n

    def edge(a: str, b: str) -> None:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for env in inv.get("environments", []) or []:
        by_name: dict[str, str] = {}
        for svc in env.get("services", []) or []:
            n = node(svc.get("container") or svc["name"], svc["name"])
            by_name[svc["name"]] = n
        for svc in env.get("services", []) or []:
            for dep in svc.get("depends_on", []) or []:
                if dep in by_name:
                    edge(by_name[svc["name"]], by_name[dep])
        hops = re.findall(r"nw-[a-z0-9-]+", env.get("request_path", "") or "")
        for a, b in zip(hops, hops[1:]):
            edge(node(a), node(b))
        for ns in env.get("namespaces", []) or []:
            names = {}
            for w in ns.get("workloads", []) or []:
                n = node(f"k8s:{ns['name']}/{w['name']}", f"deployment/{w['name']}", f"{ns['name']}/{w['name']}")
                names[w["name"]] = n
            for w in ns.get("workloads", []) or []:
                for dep in w.get("depends_on", []) or []:
                    if dep in names:
                        edge(names[w["name"]], names[dep])
    for h in inv.get("ssh_hosts", []) or []:
        units = re.findall(r"([a-z0-9-]+)\.service", h.get("role", ""))
        n = node(f"host:{h['name']}", h["name"], *units, *[f"{u}.service" for u in units])
    return alias, adj


def nodes_in(text: str) -> set[str]:
    alias, _ = graph()
    low = (text or "").lower()
    found = set()
    for a, n in alias.items():
        if re.search(rf"(?<![a-z0-9_.-]){re.escape(a)}(?![a-z0-9_-])", low):
            found.add(n)
    # "redis" alone means the redis component (container in Docker, deployment in K8s): keep the shortest aliases too.
    return found


def connected(a: str, b: str) -> bool:
    _, adj = graph()
    seen, todo = {a}, [a]
    while todo:
        x = todo.pop()
        if x == b:
            return True
        for y in adj.get(x, ()):
            if y not in seen:
                seen.add(y)
                todo.append(y)
    return False


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_.-]{3,}", (text or "").lower()) if t not in STOP}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def check_chain(chain: list[dict], outputs: list[str]) -> list[str]:
    """Return error messages for unsupported causal links. outputs = tool results of this incident."""
    errs = []
    lines = [l for o in outputs for l in (o or "").splitlines()]
    norm_lines = [_norm(l) for l in lines]
    for i, link in enumerate(chain or [], 1):
        cause, effect, quote = link.get("cause", ""), link.get("effect", ""), _norm(link.get("evidence", ""))
        if len(quote) < 12:
            errs.append(f"causal_chain[{i}]: the evidence quote is too short (min 12 characters of a tool result)")
            continue
        hits = [l for l in norm_lines if quote in l or (len(quote) > 60 and quote[:60] in l)]
        if not hits:
            errs.append(f"causal_chain[{i}] {cause} -> {effect}: the evidence quote is not in any tool result of this incident")
            continue
        a, b = nodes_in(cause), nodes_in(effect)
        if a and b and any(connected(x, y) for x in a for y in b):
            continue
        ta, tb = _tokens(cause), _tokens(effect)
        if any(any(t in h for t in ta) and any(t in h for t in tb) for h in hits):
            continue
        errs.append(f"causal_chain[{i}] {cause} -> {effect}: no dependency in the inventory, and the quoted line does not name "
                    "both. Time correlation is not causation. Remove this link, test it, or list it as an open hypothesis.")
    return errs


def unsupported_pairs(text: str) -> list[tuple[str, str]]:
    """Pairs of components named in one claim that have no dependency path in the inventory."""
    ns = sorted(nodes_in(text))
    return [(x, y) for i, x in enumerate(ns) for y in ns[i + 1:] if not connected(x, y)]
