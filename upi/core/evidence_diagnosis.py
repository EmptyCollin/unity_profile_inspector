"""证据驱动的诊断：CandidateV2 → Hypothesis。

产品默认诊断源（I9a）。边界：
- 不读夹具目录、不匹配代码文本、不读答案参照；
- 只消费运行证据生成的候选（`runs/<run>/evidence/evidence_v2.json`）；
- 没有证据就明确 `insufficient_evidence`，不退回正则猜测；
- 置信度仍由 attribution 公式计算，LLM 不参与打分。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from upi.core.attribution import FactorJudgment, build_hypothesis, rank
from upi.core.candidates_v2 import CandidateV2, generate_candidates
from upi.core.evidence_v2 import EvidenceBundleV2, EvidenceStore
from upi.core.models import Evidence


def load_latest_bundle(project_id: str = "") -> tuple[Optional[EvidenceBundleV2],
                                                      Optional[Path]]:
    """最近一份证据包；给了 project_id 就只认该工程。"""
    store = EvidenceStore()
    files = sorted(store.root.glob("*/evidence/evidence_v2.json"),
                   key=lambda p: p.stat().st_mtime)
    for p in reversed(files):
        try:
            b = store.load(p)
        except Exception:  # noqa: BLE001 — 坏包跳过，不阻断其它证据
            continue
        if project_id and b.project_id and b.project_id != project_id:
            continue
        return b, p
    return None, None


def candidates_from_latest(project_root, project_id: str = "",
                           limit: int = 12) -> tuple[list[CandidateV2],
                                                     Optional[EvidenceBundleV2],
                                                     Optional[Path]]:
    bundle, path = load_latest_bundle(project_id)
    if bundle is None:
        return [], None, None
    cands = generate_candidates(bundle, project_root=project_root, limit=limit)
    return cands, bundle, path


def _evidence_refs(cands: list[CandidateV2], path: Optional[Path]) -> list[Evidence]:
    """每个候选至少一条真实证据引用（raw_ref 指向落盘文件）。"""
    out: list[Evidence] = []
    for c in cands:
        if not c.evidence_ids:
            continue
        out.append(Evidence(tool="evidence_v2", raw_ref=str(path or ""),
                            value=float(c.score), unit="score", data_tier="A2"))
    return out


def hypotheses_from_candidates(cands: list[CandidateV2],
                               evidence_path: Optional[Path] = None) -> list:
    """候选 → 假设。因子取值来自候选的结构化事实（锚点/证据数/机制/先验），
    不来自代码阅读的主观判断；置信度由 attribution 公式算。"""
    evs = _evidence_refs(cands, evidence_path)
    hs = []
    for c in cands:
        anchors = ([f"{s.file}:{s.start_line}" for s in c.sources if s.file]
                   or list(c.code_anchors))
        ref = c.evidence_ids[0] if c.evidence_ids else (anchors[0] if anchors else "")
        js = [
            FactorJudgment(factor="proximity",
                           value=0.7 if anchors else 0.3, evidence_ref=ref),
            FactorJudgment(factor="explains_share",
                           value=0.6 if c.evidence_n >= 2 else 0.4,
                           evidence_ref=ref),
            FactorJudgment(factor="mechanism",
                           value=0.8 if c.mechanism else 0.2, evidence_ref=ref),
            FactorJudgment(factor="competitiveness", value=0.5, evidence_ref=ref),
            FactorJudgment(factor="prior_hit",
                           value=min(1.0, max(0.0, float(c.prior))),
                           evidence_ref=ref),
            FactorJudgment(factor="counterfactual", value=0.5, evidence_ref=ref),
        ]
        hs.append(build_hypothesis(c.root_cause, anchors or list(c.code_anchors),
                                   js, issue_evidence=evs))
    return rank(hs)
