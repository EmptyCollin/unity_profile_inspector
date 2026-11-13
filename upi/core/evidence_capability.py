"""I18：证据能力矩阵与批量成本规划。

目标：补采请求必须落到有能力生产它的 Agent；一次修一个改为可批处理。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Factor = Literal["proximity", "explains_share", "competitiveness",
                 "prior_hit", "counterfactual", "mechanism"]
Producer = Literal["detect", "verify", "knowledge", "human"]


@dataclass(frozen=True)
class Capability:
    factor: Factor
    producer: Producer
    kinds: tuple[str, ...]
    status: Literal["supported", "partial", "unsupported"]
    note: str = ""


# 当前实现的能力边界：不要假装能答
CAPABILITIES: dict[Factor, Capability] = {
    "proximity": Capability(
        "proximity", "detect", ("callstack", "source"), "supported"),
    "explains_share": Capability(
        "explains_share", "detect", ("per_function_share",), "partial",
        "需要 evidence graph 聚合每个候选的 total/share；目前只部分有"),
    "competitiveness": Capability(
        "competitiveness", "detect", ("candidate_ranking",), "supported"),
    "prior_hit": Capability(
        "prior_hit", "knowledge", ("signature_history",), "supported"),
    "counterfactual": Capability(
        "counterfactual", "verify", ("ablation",), "unsupported",
        "需要移除机制后重跑，检测 Agent 不能提供"),
    "mechanism": Capability(
        "mechanism", "detect", ("source", "ast_callgraph"), "supported"),
}


def capability_for(factor: str) -> Capability | None:
    return CAPABILITIES.get(factor)  # type: ignore[arg-type]


def plan_need(factor: str, mode: str) -> dict:
    """返回“该找谁要什么”；unsupported 时明确不可满足。"""
    cap = capability_for(factor)
    if cap is None:
        return {"factor": factor, "available": False,
                "reason": "未知因子", "producer": "", "kinds": []}
    if cap.producer == "detect" and cap.status == "unsupported":
        return {"factor": factor, "available": False,
                "reason": cap.note, "producer": cap.producer,
                "kinds": list(cap.kinds)}
    return {"factor": factor, "available": cap.status != "unsupported",
            "producer": cap.producer, "kinds": list(cap.kinds),
            "note": cap.note}
