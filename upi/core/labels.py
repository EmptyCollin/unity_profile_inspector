"""标签库（I12e）：唯一入库通道 = 归纳总结 Agent 提议 + 用户 UI 批准/驳回。

- 标签按 project_id 隔离；
- proposed/rejected 不得用于评分/训练；
- 重复（机制+锚点+帧桶）不再提议；驳回后不自动重提；
- 工具/Agent 不得自动批准。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from upi.core import events as E


class LabelProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label_id: str = ""
    project_id: str
    run_id: str = ""
    mechanism: str
    file: str = ""
    method: str = ""
    line: int = 0
    frame_start: int = 0
    frame_end: int = 0
    metric: str = ""
    threshold: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""
    source: str = "summarizer"
    status: str = "proposed"          # proposed | approved | rejected | retracted
    proposed_by: str = "summarizer"
    proposed_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    decision_reason: str = ""
    version: int = 1


def _safe(pid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in pid) or "_"


def default_path(project_id: str) -> Path:
    return Path(E.RUNS) / "projects" / _safe(project_id) / "labels" / "labels.db"


class LabelStore:
    def __init__(self, project_id: str, path: Optional[Path] = None):
        self.project_id = project_id
        self.path = Path(path) if path else default_path(project_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        c = sqlite3.connect(str(self.path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("""
              CREATE TABLE IF NOT EXISTS labels (
                label_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                run_id TEXT, mechanism TEXT NOT NULL,
                file TEXT, method TEXT, line INTEGER,
                frame_start INTEGER, frame_end INTEGER,
                metric TEXT, threshold TEXT,
                evidence_refs TEXT, confidence REAL, rationale TEXT,
                source TEXT, status TEXT,
                proposed_by TEXT, proposed_at TEXT,
                reviewed_by TEXT, reviewed_at TEXT,
                decision_reason TEXT, version INTEGER
              )""")

    @staticmethod
    def _bucket(frame: int) -> int:
        return int(frame) // 300

    def _find(self, p: LabelProposal):
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM labels WHERE project_id=? AND mechanism=?"
                " AND file=? AND method=? AND frame_start/300=? LIMIT 1",
                (self.project_id, p.mechanism, p.file, p.method,
                 self._bucket(p.frame_start))).fetchone()

    def propose(self, p: LabelProposal) -> tuple[Optional[dict], str]:
        """返回 (proposal, reason)。已有/已驳回 → (None, reason)。"""
        p.project_id = self.project_id
        p.file = (p.file or "").replace("\\", "/")
        p.method = (p.method or "").split("(")[0].rsplit(".", 1)[-1]
        old = self._find(p)
        if old is not None:
            return None, f"已存在标签 {old['label_id']}（status={old['status']}），不再提议"
        p.label_id = p.label_id or ("lb-" + time.strftime("%Y%m%d_%H%M%S")
                                    + "-" + str(abs(hash(p.model_dump_json())))[-5:])
        p.proposed_at = p.proposed_at or E.now()
        p.status = "proposed"
        with self._conn() as c:
            c.execute(
                "INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p.label_id, p.project_id, p.run_id, p.mechanism, p.file,
                 p.method, p.line, p.frame_start, p.frame_end, p.metric,
                 p.threshold, json.dumps(p.evidence_refs, ensure_ascii=False),
                 p.confidence, p.rationale, p.source, p.status,
                 p.proposed_by, p.proposed_at, "", "", "", p.version))
        return self.get(p.label_id), "proposed"

    def get(self, label_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM labels WHERE label_id=?",
                            (label_id,)).fetchone()
        return self._row(row) if row else None

    def _row(self, r) -> dict:
        d = dict(r)
        try:
            d["evidence_refs"] = json.loads(d.get("evidence_refs") or "[]")
        except Exception:  # noqa: BLE001
            d["evidence_refs"] = []
        return d

    def list(self, status: str = "") -> list[dict]:
        q = "SELECT * FROM labels WHERE project_id=?"
        args = [self.project_id]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY proposed_at DESC"
        with self._conn() as c:
            return [self._row(r) for r in c.execute(q, args)]

    def pending(self) -> list[dict]:
        return self.list("proposed")

    def active(self) -> list[dict]:
        return self.list("approved")

    def decide(self, label_id: str, *, decision: str, reviewer: str,
               reason: str = "") -> tuple[bool, str]:
        """只有用户能调用；只允许 proposed → approved/rejected。"""
        if decision not in ("approve", "reject"):
            return False, "decision 必须是 approve/reject"
        if not (reviewer or "").strip():
            return False, "缺少审批人"
        rec = self.get(label_id)
        if rec is None:
            return False, "标签不存在"
        if rec["status"] != "proposed":
            return False, f"标签状态 {rec['status']} 不可审批"
        new_status = "approved" if decision == "approve" else "rejected"
        with self._conn() as c:
            c.execute("UPDATE labels SET status=?, reviewed_by=?, reviewed_at=?,"
                      " decision_reason=? WHERE label_id=? AND status='proposed'",
                      (new_status, reviewer, E.now(), reason, label_id))
        return True, new_status
