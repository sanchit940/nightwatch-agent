"""Evidence and audit store (SQLite). The event log is append-only and hash-chained: each row stores the
hash of the previous row, so a changed or deleted row breaks the chain (see verify_chain)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  incident_id TEXT,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_incident ON events(incident_id);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY,
  opened_at REAL NOT NULL,
  symptom TEXT NOT NULL,
  source TEXT NOT NULL,
  dedup_key TEXT,
  status TEXT NOT NULL,
  closed_at REAL,
  report TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  incident_id TEXT,
  action TEXT NOT NULL,
  params TEXT NOT NULL,
  targets TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  justification TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  digest TEXT NOT NULL,
  status TEXT NOT NULL,
  approver TEXT,
  token TEXT,
  decided_at REAL,
  executed_at REAL,
  result TEXT
);
"""


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class Audit:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ----- hash-chained event log -----
    def log(self, kind: str, payload: dict[str, Any], incident_id: str | None = None) -> int:
        with self._lock:
            row = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "genesis"
            ts = time.time()
            body = _canon(payload)
            h = hashlib.sha256(f"{prev}|{ts}|{incident_id}|{kind}|{body}".encode()).hexdigest()
            cur = self.db.execute(
                "INSERT INTO events(ts, incident_id, kind, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                (ts, incident_id, kind, body, prev, h))
            return cur.lastrowid

    def events(self, incident_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM events" + (" WHERE incident_id=?" if incident_id else "") + " ORDER BY seq"
        rows = self.db.execute(q, (incident_id,) if incident_id else ()).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]

    def verify_chain(self) -> tuple[bool, int | None]:
        prev = "genesis"
        for r in self.db.execute("SELECT * FROM events ORDER BY seq"):
            h = hashlib.sha256(f"{prev}|{r['ts']}|{r['incident_id']}|{r['kind']}|{r['payload']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return False, r["seq"]
            prev = r["hash"]
        return True, None

    # ----- incidents -----
    def open_incident(self, symptom: str, source: str, dedup_key: str | None = None) -> str:
        iid = "inc-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.db.execute("INSERT INTO incidents(id, opened_at, symptom, source, dedup_key, status) VALUES (?,?,?,?,?,?)",
                        (iid, time.time(), symptom, source, dedup_key, "investigating"))
        self.log("incident_opened", {"symptom": symptom, "source": source, "dedup_key": dedup_key}, iid)
        return iid

    def find_open_incident(self, dedup_key: str) -> str | None:
        r = self.db.execute("SELECT id FROM incidents WHERE dedup_key=? AND status NOT IN ('resolved','closed') "
                            "ORDER BY opened_at DESC LIMIT 1", (dedup_key,)).fetchone()
        return r["id"] if r else None

    def set_incident(self, iid: str, status: str, report: dict | None = None) -> None:
        closed = time.time() if status in ("resolved", "closed") else None
        self.db.execute("UPDATE incidents SET status=?, report=COALESCE(?, report), closed_at=COALESCE(?, closed_at) WHERE id=?",
                        (status, _canon(report) if report else None, closed, iid))
        self.log("incident_status", {"status": status}, iid)

    def incident(self, iid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def incidents(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM incidents ORDER BY opened_at DESC LIMIT ?", (limit,))]

    # ----- approvals -----
    def insert_approval(self, rec: dict) -> None:
        self.db.execute(
            "INSERT INTO approvals(id, incident_id, action, params, targets, fingerprint, justification, created_at, expires_at, digest, status) "
            "VALUES (:id,:incident_id,:action,:params,:targets,:fingerprint,:justification,:created_at,:expires_at,:digest,:status)",
            {**rec, "params": _canon(rec["params"]), "targets": _canon(rec["targets"]), "justification": _canon(rec["justification"])})

    def approval(self, aid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("params", "targets", "justification", "result"):
            if d.get(k):
                d[k] = json.loads(d[k])
        return d

    def approvals(self, status: str | None = None, incident_id: str | None = None) -> list[dict]:
        q, args = "SELECT id FROM approvals WHERE 1=1", []
        if status:
            q += " AND status=?"; args.append(status)
        if incident_id:
            q += " AND incident_id=?"; args.append(incident_id)
        return [self.approval(r["id"]) for r in self.db.execute(q + " ORDER BY created_at", args)]

    def update_approval(self, aid: str, **fields: Any) -> None:
        for k in ("result",):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = _canon(fields[k])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE approvals SET {sets} WHERE id=?", (*fields.values(), aid))

    def count_executed(self, action: str, target: str, since: float) -> int:
        rows = self.db.execute("SELECT targets FROM approvals WHERE action=? AND executed_at>=?", (action, since)).fetchall()
        return sum(1 for r in rows if target in json.loads(r["targets"]))
