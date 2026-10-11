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


def _loose(s: str) -> str:
    """Letters and digits only: ignores JSON quotes, escapes, punctuation and line breaks."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _records(output: str) -> list[str]:
    """Evidence units. An optional first line "@@ <tool> <args>" names the tool call; it is added to every record.
    One whole JSON result is one record; otherwise each line (log line, event) is one record."""
    o = (output or "").strip()
    head = ""
    if o.startswith("@@ "):
        head, _, o = o.partition("\n")
        head, o = head[3:], o.strip()
    if o.startswith(("{", "[")):
        return [f"{head} {o}"]
    return [f"{head} {l}" for l in o.splitlines() if l.strip()]


def _distinct(text: str) -> set[str]:
    """Specific values of a quote: numbers, IDs and names. Plain verbs and filler words do not count."""
    toks = set()
    for t in re.findall(r"[a-z0-9][a-z0-9_.:/=-]*[a-z0-9]|\d+", (text or "").lower()):
        if t in STOP:
            continue
        if any(c.isdigit() for c in t) or any(c in t for c in "-_.:/="):
            toks.add(t)
    return toks


def _pieces(quote: str) -> list[str]:
    """Models combine sources ("docker_events: ...; docker_inspect: ...") and shorten quotes with "...".
    Split the evidence into pieces: the whole text, each part between ';', and each quoted text split at '...'."""
    parts = [quote] + [p for p in re.split(r";\s*|\s+\|\s+", quote) if p.strip()]
    for q in re.findall(r"'([^']{8,})'|\"([^\"]{8,})\"", quote):
        parts += [x for x in re.split(r"\.\.\.|…", q[0] or q[1]) if len(x.strip()) >= 8]
    return list(dict.fromkeys(p.strip() for p in parts))


def _find(quote: str, outputs: list[str]) -> list[str]:
    """Records (see _records) that support at least one piece of the evidence: the piece text itself (compared
    loosely, min 12 characters), or ALL specific values of the piece (at least 3) inside ONE record. Values spread
    over two neighbouring event lines never match one record: that is time correlation."""
    recs = [_loose(r) for o in outputs for r in _records(o)]
    hits = []
    for piece in _pieces(quote):
        q = _loose(piece)
        if len(q) < 10:
            continue
        want = {_loose(t) for t in _distinct(piece)}
        for lr in recs:
            if (len(q) >= 12 and q in lr) or (len(want) >= 3 and all(w in lr for w in want)):
                hits.append(lr)
    return list(dict.fromkeys(hits))


def check_chain(chain: list[dict], outputs: list[str]) -> list[str]:
    """Return error messages for unsupported causal links. outputs = tool results of this incident."""
    errs = []
    for i, link in enumerate(chain or [], 1):
        cause, effect, quote = link.get("cause", ""), link.get("effect", ""), link.get("evidence", "")
        if len(_loose(quote)) < 10:
            errs.append(f"causal_chain[{i}]: the evidence quote is too short (copy at least 12 characters of a tool result)")
            continue
        hits = _find(quote, outputs)
        if not hits:
            errs.append(f"causal_chain[{i}] {cause} -> {effect}: the evidence quote is not in any tool result of this incident")
            continue
        a, b = nodes_in(cause), nodes_in(effect)
        if not (a and b) or a & b:
            # A false link between two different components is the risk. A link to a pure symptom ("all requests
            # return 503") or inside one component only needs proven evidence.
            continue
        if any(connected(x, y) for x in a for y in b):
            continue
        ta, tb = {_loose(t) for t in _tokens(cause)}, {_loose(t) for t in _tokens(effect)}
        if any(any(t and t in h for t in ta) and any(t and t in h for t in tb) for h in hits):
            continue
        errs.append(f"causal_chain[{i}] {cause} -> {effect}: no dependency in the inventory, and the quoted text does not name "
                    "both. Time correlation is not causation. Remove this link, test it, or list it as an open hypothesis.")
    return errs


def split_chain(chain: list[dict], outputs: list[str]) -> tuple[list[dict], list[dict]]:
    """(proven links, unproven links). Used when the model cannot prove a link after retries."""
    good, bad = [], []
    for link in chain or []:
        (bad if check_chain([link], outputs) else good).append(link)
    return good, bad


def unsupported_pairs(text: str) -> list[tuple[str, str]]:
    """Pairs of components named in one claim that have no dependency path in the inventory."""
    ns = sorted(nodes_in(text))
    return [(x, y) for i, x in enumerate(ns) for y in ns[i + 1:] if not connected(x, y)]
