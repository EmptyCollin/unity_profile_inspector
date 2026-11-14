"""I20：目标级验证谓词。

verified = metric 显著改善 + 机制不变量成立 + 站点清零
函数是否还在候选不再单独决定。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TargetSpec:
    finding_id: str
    mechanism: str
    metric: str
    baseline_values: list[float] = field(default_factory=list)
    min_delta: float = 0.0
    k_sigma: float = 3.0
    invariant: str = ""


@dataclass
class TargetObservation:
    after: Optional[float] = None
    invariant_ok: bool = True
    sites_remaining: int = 0
    new_clusters: list[str] = field(default_factory=list)
    guards_ok: bool = True
    pseudo: bool = False


@dataclass
class TargetVerdict:
    status: str
    delta: Optional[float] = None
    threshold: Optional[float] = None
    reason: str = ""


def evaluate_target(spec: TargetSpec, obs: TargetObservation) -> TargetVerdict:
    if obs.after is None or not spec.baseline_values:
        return TargetVerdict("unmeasured", reason="metric 缺失")
    if len(spec.baseline_values) >= 2:
        sigma = statistics.pstdev(spec.baseline_values)
    else:
        sigma = 0.0
    before = statistics.median(spec.baseline_values)
    # Profiler 采样序列噪声大：σ 超过中位数 5% 时改用相对阈值，
    # 否则稳定指标仍用 3σ。不同 metric 的合理改善幅度不同。
    if before and sigma > 0.05 * abs(before):
        rel = {"frame_time": 0.05, "cpu_frame_time": 0.05,
               "memory": 0.05, "drawcalls": 0.05,
               "gc_alloc": 0.10}.get(spec.metric, 0.05)
        threshold = max(spec.min_delta, rel * abs(before))
    else:
        threshold = max(spec.min_delta, spec.k_sigma * sigma)
    delta = before - obs.after
    if delta < threshold:
        return TargetVerdict("needs_recheck", delta, threshold,
                             "metric 改善未过阈值")
    if not obs.invariant_ok:
        return TargetVerdict("pseudo" if obs.pseudo else "partial",
                             delta, threshold,
                             f"机制不变量未满足：{spec.invariant}")
    if obs.sites_remaining > 0:
        return TargetVerdict("partial", delta, threshold,
                             f"仍有 {obs.sites_remaining} 个站点")
    if obs.new_clusters or not obs.guards_ok:
        return TargetVerdict("needs_recheck", delta, threshold,
                             "新 cluster 或守护劣化")
    return TargetVerdict("verified", delta, threshold,
                         "metric+不变量+站点通过")
