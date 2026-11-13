"""I18：批量修复与成本估算。

独立病例可分组：一组一次 after 采集，逐 patch 快照，失败二分回滚。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class CaseCost:
    case_id: str
    files: tuple[str, ...] = ()
    mechanism: str = ""


@dataclass
class Batch:
    cases: list[CaseCost] = field(default_factory=list)


def conflict(a: CaseCost, b: CaseCost) -> bool:
    return bool(set(a.files) & set(b.files)) or (
        a.mechanism and a.mechanism == b.mechanism)


def plan_batches(cases: list[CaseCost], batch_size: int = 4) -> list[Batch]:
    batches: list[Batch] = []
    for c in cases:
        placed = False
        for b in batches:
            if len(b.cases) >= batch_size:
                continue
            if any(conflict(c, x) for x in b.cases):
                continue
            b.cases.append(c)
            placed = True
            break
        if not placed:
            batches.append(Batch([c]))
    return batches


@dataclass
class CostEstimate:
    cases: int
    batches: int
    captures: int
    apply_ops: int
    wallclock_s: float
    baseline_s: float


def estimate(cases: int, *, batch_size: int = 4, baseline_s: float = 150.0,
             capture_s: float = 140.0, apply_s: float = 20.0) -> CostEstimate:
    batches = max(1, math.ceil(cases / max(1, batch_size)))
    captures = batches + 1  # after captures + baseline
    return CostEstimate(cases=cases, batches=batches, captures=captures,
                        apply_ops=cases,
                        wallclock_s=baseline_s + batches * capture_s
                        + cases * apply_s,
                        baseline_s=baseline_s)
