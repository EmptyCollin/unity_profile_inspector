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


def anchors_of(c: CandidateV2) -> list[str]:
    """候选 → 可规划锚点：`file::symbol`（方法）+ `file:line`（定位）。

    深档候选可能带绝对路径/程序集符号：这里统一归一成工程内相对路径，
    避免把运行目录/工程名带进提示词（泄漏筛查会拒发）。
    """
    from upi.core.findings import normalize_file, normalize_method
    anchors: list[str] = []
    for s in c.sources:
        if not s.file:
            continue
        f = normalize_file(s.file)
        if s.symbol:
            anchors.append(f"{f}::{normalize_method(s.symbol)}")
        if s.start_line:
            anchors.append(f"{f}:{s.start_line}")
    return list(dict.fromkeys(anchors)) or [normalize_file(a) for a in c.code_anchors]


def candidates_from_latest(project_root, project_id: str = "",
                           limit: int = 12) -> tuple[list[CandidateV2],
                                                     Optional[EvidenceBundleV2],
                                                     Optional[Path]]:
    bundle, path = load_latest_bundle(project_id)
    if bundle is None:
        return [], None, None
    cands = generate_candidates(bundle, project_root=project_root, limit=limit)
    return cands, bundle, path
