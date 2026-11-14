"""I24：修复契约与合算性判定。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuardBudget:
    metric: str
    max_relative: float = 0.05
    max_absolute: float | None = None
    higher_is_better: bool = False


@dataclass
class RepairContract:
    primary_metric: str = "frame_time"
    min_improve: float = 0.05
    guards: list[GuardBudget] = field(default_factory=list)
    mb_per_ms: float = 8.0
    min_net: float = 0.0


@dataclass
class ContractResult:
    status: str
    primary_improve: float
    net: float
    guard_violations: list[str]
    details: dict


def evaluate(contract: RepairContract, *, primary_before: float,
             primary_after: float,
             guards: dict[str, tuple[float, float]] | None = None) -> ContractResult:
    guards = guards or {}
    if primary_before <= 0:
        return ContractResult("unmeasured", 0.0, 0.0, [], {})
    improve = (primary_before - primary_after) / primary_before
    if improve < contract.min_improve:
        return ContractResult("needs_recheck", improve, 0.0, [],
                              {"reason": "primary_not_significant"})
    violations = []
    for g in contract.guards:
        pair = guards.get(g.metric)
        if not pair:
            continue
        before, after = pair
        if before <= 0:
            continue
        regress = (after - before) / before if not g.higher_is_better \
            else (before - after) / before
        allowed = g.max_relative
        if g.max_absolute is not None:
            allowed = max(allowed, g.max_absolute / before)
        if regress > allowed:
            violations.append(g.metric)
    if violations:
        return ContractResult("regressed", improve, 0.0, violations,
                              {"violations": violations})
    # frame_time + memory 的显式汇率
    net = improve
    details = {"reason": "normalized"}
    if (contract.primary_metric in ("frame_time", "cpu_frame_time")
            and "memory" in guards):
        b, a = guards["memory"]
        frame_saved_ms = primary_before - primary_after
        mem_growth_mb = max(0.0, (a - b) / (1024 * 1024))
        cost_ms = mem_growth_mb / max(contract.mb_per_ms, 1e-9)
        net_ms = frame_saved_ms - cost_ms
        net = net_ms / max(primary_before, 1e-9)
        details = {"frame_saved_ms": frame_saved_ms,
                   "mem_growth_mb": mem_growth_mb,
                   "cost_ms": cost_ms,
                   "net_ms": net_ms}
    if net <= contract.min_net:
        return ContractResult("needs_recheck", improve, net, [], details)
    status = ("verified_with_tradeoff" if net < improve else "verified")
    return ContractResult(status, improve, net, [], details)
