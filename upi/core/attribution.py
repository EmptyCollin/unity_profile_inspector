"""归因与置信度（M2，DESIGN §5 / AGENTS §1.3）。

分工纪律（§1.3 硬规则的代码落点）：
- LLM 只判定六因子的"是否成立"（可核查判断）并给证据指针；
- C_attr 由公式算出，LLM 不打分；
- Top1 < T_attr_min(0.6) → needs_human，不进自动修复。

权重现状：hand-set-prior-v1（人工先验，DESIGN §5 要求报告如实标注）。
n_effective≥30 前禁止拟合（§8.4），届时递增 weights_version，
否则历史置信度不可比。
"""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, Field

from upi.core.models import AttributionFactors, Hypothesis, Issue

T_ATTR_MIN = 0.6                # §1.3：Top1 低于此 → needs_human
VETO_CAP = 0.5                  # 否决命中 → 强制 ≤0.5，不允许 auto

WEIGHTS: dict[str, float] = {
    "w0": -1.5,                 # 截距：无因子时先验压到 0.18（不许白手起家）
    "proximity": 0.6,           # 有效但易误判（被调用方陷阱），权重压低
    "explains_share": 2.2,      # 最关键：定量对上才算归因
    "competitiveness": 0.8,
    "prior_hit": 1.2,
    "counterfactual": 1.5,      # 实验证据 > 推理证据
    "mechanism": 1.0,
}
WEIGHTS_VERSION = "hand-set-prior-v1"


class FactorJudgment(BaseModel):
    """LLM 对单因子的结构化判定：值 + 证据指针（不是分数本身）。"""

    factor: Literal["proximity", "explains_share", "competitiveness",
                    "prior_hit", "counterfactual", "mechanism"]
    value: float = Field(ge=0, le=1)
    evidence_ref: str = ""          # 指向 runs/<id>/evidence/...；空=仅代码阅读
    counterfactual_tested: bool = False   # 仅 counterfactual 因子有意义
    counterfactual_disproved: bool = False


def veto_reasons(f: AttributionFactors, js: list[FactorJudgment],
                 issue_evidence: list) -> list[str]:
    """DESIGN §5 硬性否决项，任一命中 → C_attr 强制 ≤0.5。"""
    out: list[str] = []
    if f.explains_share < 0.3:
        out.append("explains_share < 0.3（解释力不足，定量对不上）")
    if js and all(not j.evidence_ref for j in js):
        out.append("全部因子判定无证据指针，仅凭代码阅读")
    if not issue_evidence:
        out.append("Issue 无 Evidence 支撑")
    cf = next((j for j in js if j.factor == "counterfactual"), None)
    if cf and cf.counterfactual_tested and cf.counterfactual_disproved:
        out.append("反事实实验做了且被否证")
    return out


def c_attr(f: AttributionFactors) -> float:
    """逻辑回归式加权：logit = w0 + Σ wi·fi。"""
    logit = WEIGHTS["w0"] + sum(WEIGHTS[k] * getattr(f, k)
                                for k in ("proximity", "explains_share",
                                          "competitiveness", "prior_hit",
                                          "counterfactual", "mechanism"))
    return 1.0 / (1.0 + math.exp(-logit))


def build_hypothesis(root_cause: str, code_anchors: list[str],
                     judgments: list[FactorJudgment], issue: Issue,
                     expected_effect: Optional[dict] = None) -> Hypothesis:
    """因子判定 → Hypothesis（置信度在构造前算好，含否决封顶）。

    取值以 factor 枚举为准白名单过滤（FactorJudgment.factor 已是 Literal，
    但 dict 推导若遇重复因子取先到者，保证构造不因多余键崩）。"""
    fmap: dict[str, float] = {}
    for j in judgments:
        fmap.setdefault(j.factor, j.value)
    keys = ("proximity", "explains_share", "competitiveness",
            "prior_hit", "counterfactual", "mechanism")
    f = AttributionFactors(**{k: fmap.get(k, 0.0) for k in keys})
    conf = c_attr(f)
    vr = veto_reasons(f, judgments, issue.evidence)
    if vr:
        conf = min(conf, VETO_CAP)
    ev = expected_effect or {}
    return Hypothesis(root_cause=root_cause, code_anchors=code_anchors,
                      attribution_factors=f, confidence=round(conf, 4),
                      weights_version=WEIGHTS_VERSION,
                      expected_effect=ev,
                      veto_reasons=vr)


def rank(hs: list[Hypothesis]) -> list[Hypothesis]:
    return sorted(hs, key=lambda h: h.confidence, reverse=True)


def route(decision: list[Hypothesis]) -> str:
    """AGENTS §1.3：Top1 < T_attr_min → needs_human。"""
    if not decision:
        return "needs_human"
    return "auto_or_semi" if decision[0].confidence >= T_ATTR_MIN else "needs_human"
