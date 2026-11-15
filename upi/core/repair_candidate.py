"""I26：修复候选与语义风险门禁。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepairCandidate:
    mechanism: str
    site_id: str
    metric: str
    window: str
    expected_saved_ms: float = 0.0
    expected_saved_bytes: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    semantic_risk: str = ""
    required_behavior_test: str = ""
    patch_family: str = ""

    def validate(self) -> list[str]:
        errs = []
        if not self.mechanism:
            errs.append("missing_mechanism")
        if not self.site_id:
            errs.append("missing_site")
        if not self.metric:
            errs.append("missing_metric")
        if not self.window:
            errs.append("missing_window")
        if not self.evidence_refs:
            errs.append("missing_evidence")
        if self.expected_saved_ms <= 0 and self.expected_saved_bytes <= 0:
            errs.append("missing_expected_saving")
        if self.semantic_risk and not self.required_behavior_test:
            errs.append("semantic_risk_without_test")
        return errs

    def score(self) -> float:
        penalty = 0.5 if self.semantic_risk else 0.0
        return self.expected_saved_ms + self.expected_saved_bytes / 1024.0 - penalty


def semantic_risk_for_diffs(diffs: list[dict]) -> str:
    risk = ""
    for d in diffs:
        find = str(d.get("find") or "")
        repl = str(d.get("replace") or "")
        if not repl.strip():
            risk = "empty_replace"
        elif len(find) - len(repl) > 400:
            risk = "block_delete"
    return risk


def rank_candidates(items: list[RepairCandidate]) -> list[RepairCandidate]:
    return sorted(items, key=lambda c: c.score(), reverse=True)
