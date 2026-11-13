"""归纳总结 Agent（I12e）：把一次 run 的诊断/修复归纳成标签提议。

只提议，不批准；重复查询标签库；驳回后不自动重提。
"""
from __future__ import annotations

from typing import Optional

from upi.core.findings import RunFindings
from upi.core.labels import LabelProposal, LabelStore


class SummarizerProposer:
    def __init__(self, project_id: str, store: Optional[LabelStore] = None):
        self.project_id = project_id
        self.store = store or LabelStore(project_id)

    def propose(self, run: RunFindings) -> dict:
        created, skipped = [], []
        for f in run.findings:
            if not f.anchors:
                continue
            a = f.anchors[0]
            repair = next((r for r in reversed(run.repairs)
                           if r.finding_id == f.finding_id), None)
            proposal = LabelProposal(
                project_id=self.project_id, run_id=run.run_id,
                mechanism=f.mechanism, file=a.file, method=a.method or "",
                line=a.start_line or 0, frame_start=0, frame_end=0,
                metric=f.metric,
                threshold=str((f.attribution.factors or {}).get(
                    "threshold", "")),
                evidence_refs=list(f.evidence_refs),
                confidence=float(f.attribution.confidence or 0),
                rationale=(f"run {run.run_id}；"
                           + (f"修复 verdict={repair.verdict}"
                              if repair else "未修复")),
                source="summarizer")
            rec, why = self.store.propose(proposal)
            (created if rec is not None else skipped).append(
                rec["label_id"] if rec is not None else why)
        return {"created": created, "skipped": skipped,
                "pending": len(self.store.pending()),
                "active": len(self.store.active())}
