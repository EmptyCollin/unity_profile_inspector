"""I16：多病例修复队列。

每个病例独立 attempt / 状态；一个病例失败不回滚其它病例。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

CaseStatus = Literal["detected", "diagnosed", "planned", "applied",
                     "verified", "partial_fix", "failed", "skipped"]


@dataclass
class CaseState:
    case_id: str
    finding_id: str
    status: CaseStatus = "detected"
    attempts: int = 0
    max_attempts: int = 3
    verdict: str = ""
    reason: str = ""
    snapshot_ref: str = ""
    patch_id: str = ""

    def can_attempt(self) -> bool:
        return self.status not in ("verified", "failed", "skipped") \
            and self.attempts < self.max_attempts


@dataclass
class RepairQueue:
    cases: list[CaseState] = field(default_factory=list)
    max_cases: int = 11

    def add(self, case_id: str, finding_id: str, *, max_attempts: int = 3,
            status: CaseStatus = "detected") -> CaseState:
        st = CaseState(case_id=case_id, finding_id=finding_id,
                       max_attempts=max_attempts, status=status)
        self.cases.append(st)
        return st

    def next_pending(self) -> Optional[CaseState]:
        for c in self.cases[:self.max_cases]:
            if c.can_attempt() and c.status in ("detected", "diagnosed",
                                                "planned", "applied"):
                return c
        return None

    def mark(self, case_id: str, status: CaseStatus, *,
             verdict: str = "", reason: str = "",
             snapshot_ref: str = "", patch_id: str = "") -> CaseState:
        c = self.get(case_id)
        if c is None:
            raise KeyError(case_id)
        c.status = status
        if verdict:
            c.verdict = verdict
        if reason:
            c.reason = reason
        if snapshot_ref:
            c.snapshot_ref = snapshot_ref
        if patch_id:
            c.patch_id = patch_id
        return c

    def get(self, case_id: str) -> Optional[CaseState]:
        return next((c for c in self.cases if c.case_id == case_id), None)

    def summary(self) -> dict:
        out: dict[str, int] = {}
        for c in self.cases:
            out[c.status] = out.get(c.status, 0) + 1
        return {"total": len(self.cases), "by_status": out,
                "verified": out.get("verified", 0),
                "target": min(9, len(self.cases))}

    def target_met(self) -> bool:
        return self.summary()["verified"] >= self.summary()["target"]
