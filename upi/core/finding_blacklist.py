"""I25：finding 黑名单指纹库。

只用于生产调度；考试不得读取。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = {"proposed", "active", "expired", "retired"}


@dataclass
class BlacklistFingerprint:
    signature: str
    window: str
    metric: str
    protocol_fingerprint: str
    project_scope: str = ""

    def fingerprint_id(self) -> str:
        raw = json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)
        return "bl-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class BlacklistEntry:
    fingerprint_id: str
    signature: str
    window: str
    metric: str
    protocol_fingerprint: str
    project_scope: str = ""
    reason: str = ""
    attempts: int = 0
    last_seen: str = ""
    owner: str = ""
    ttl: int = 0
    status: str = "proposed"
    audit: list[dict] = field(default_factory=list)


class BlacklistStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        c = sqlite3.connect(str(self.path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("""
              CREATE TABLE IF NOT EXISTS blacklist (
                fingerprint_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                window TEXT DEFAULT '',
                metric TEXT DEFAULT '',
                protocol_fingerprint TEXT DEFAULT '',
                project_scope TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                attempts INTEGER DEFAULT 0,
                last_seen TEXT DEFAULT '',
                owner TEXT DEFAULT '',
                ttl INTEGER DEFAULT 0,
                status TEXT DEFAULT 'proposed',
                created_at REAL DEFAULT 0
              )
            """)
            c.execute("""
              CREATE TABLE IF NOT EXISTS blacklist_audit (
                fingerprint_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                actor TEXT DEFAULT '',
                action TEXT DEFAULT '',
                detail TEXT DEFAULT ''
              )
            """)
            c.commit()

    def _row(self, r) -> BlacklistEntry:
        return BlacklistEntry(
            fingerprint_id=r["fingerprint_id"], signature=r["signature"],
            window=r["window"] or "", metric=r["metric"] or "",
            protocol_fingerprint=r["protocol_fingerprint"] or "",
            project_scope=r["project_scope"] or "",
            reason=r["reason"] or "", attempts=r["attempts"] or 0,
            last_seen=r["last_seen"] or "", owner=r["owner"] or "",
            ttl=r["ttl"] or 0, status=r["status"] or "proposed")

    def upsert(self, fp: BlacklistFingerprint, *, status: str = "proposed",
               reason: str = "", attempts: int = 0, owner: str = "",
               ttl: int = 0, actor: str = "") -> BlacklistEntry:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        fid = fp.fingerprint_id()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as c:
            c.execute("""
              INSERT INTO blacklist (fingerprint_id, signature, window, metric,
                protocol_fingerprint, project_scope, reason, attempts, last_seen,
                owner, ttl, status, created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(fingerprint_id) DO UPDATE SET
                reason=excluded.reason, attempts=excluded.attempts,
                last_seen=excluded.last_seen, owner=excluded.owner,
                ttl=excluded.ttl, status=excluded.status
            """, (fid, fp.signature, fp.window, fp.metric,
                  fp.protocol_fingerprint, fp.project_scope, reason, attempts,
                  now, owner, ttl, status, time.time()))
            c.execute("""INSERT INTO blacklist_audit
                         (fingerprint_id, ts, actor, action, detail)
                         VALUES (?,?,?,?,?)""",
                      (fid, now, actor, f"upsert:{status}", reason))
            c.commit()
        return self.get(fid)

    def get(self, fid: str) -> BlacklistEntry | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM blacklist WHERE fingerprint_id=?",
                          (fid,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> list[BlacklistEntry]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM blacklist ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, fid: str, status: str, *, actor: str = "",
                   reason: str = "") -> BlacklistEntry:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as c:
            c.execute("UPDATE blacklist SET status=? WHERE fingerprint_id=?",
                      (status, fid))
            c.execute("""INSERT INTO blacklist_audit
                         (fingerprint_id, ts, actor, action, detail)
                         VALUES (?,?,?,?,?)""",
                      (fid, now, actor, f"status:{status}", reason))
            c.commit()
        return self.get(fid)

    def delete(self, fid: str, *, actor: str = "", reason: str = "") -> bool:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as c:
            cur = c.execute("DELETE FROM blacklist WHERE fingerprint_id=?", (fid,))
            c.execute("""INSERT INTO blacklist_audit
                         (fingerprint_id, ts, actor, action, detail)
                         VALUES (?,?,?,?,?)""",
                      (fid, now, actor, "delete", reason))
            c.commit()
        return cur.rowcount > 0

    def expire_due(self, now: float | None = None) -> int:
        now = now or time.time()
        n = 0
        for e in self.all():
            if e.status == "active" and e.ttl and (now - self._created(e.fingerprint_id)) > e.ttl:
                self.set_status(e.fingerprint_id, "expired", actor="system",
                                reason="ttl")
                n += 1
        return n

    def _created(self, fid: str) -> float:
        with self._conn() as c:
            r = c.execute("SELECT created_at FROM blacklist WHERE fingerprint_id=?",
                          (fid,)).fetchone()
        return float(r["created_at"]) if r else 0.0

    def lookup(self, fp: BlacklistFingerprint,
               now: float | None = None) -> BlacklistEntry | None:
        e = self.get(fp.fingerprint_id())
        if not e or e.status != "active":
            return None
        if e.ttl and (now or time.time()) - self._created(e.fingerprint_id) > e.ttl:
            self.set_status(e.fingerprint_id, "expired", actor="system",
                            reason="ttl")
            return None
        return e
