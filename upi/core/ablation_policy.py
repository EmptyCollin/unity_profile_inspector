"""I26-ext E4：消融需要的判定（策略由用户配置，LLM 只能申请）。"""
from __future__ import annotations


def decide(*, measured: bool, semantic_risk: str = "",
           causal_ambiguity: float = 0.0, ambiguity_threshold: float = 0.5,
           expected_gain: float | None = None, threshold: float = 0.0,
           margin: float = 0.25, competing_candidates: int = 0,
           result_contradiction: bool = False,
           expected_value: float = 0.0,
           ablation_cost: float = 0.0) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not measured:
        return "forbidden", ["not_measured"]
    if semantic_risk:
        return "forbidden", ["semantic_risk"]
    if causal_ambiguity >= ambiguity_threshold:
        reasons.append("causal_ambiguity")
    if expected_gain is not None and abs(expected_gain - threshold) <= margin:
        reasons.append("near_threshold")
    if competing_candidates >= 2:
        reasons.append("competing_candidates")
    if result_contradiction:
        reasons.append("result_contradiction")
    if not reasons:
        return "forbidden", ["no_trigger"]
    if expected_value <= ablation_cost:
        return "forbidden", ["cost_exceeds_value"]
    if "causal_ambiguity" in reasons or "result_contradiction" in reasons:
        return "required", reasons
    return "optional", reasons
