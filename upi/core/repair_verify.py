"""I24：verified 判定。invariant 只是必要条件。"""
from __future__ import annotations


def decide(*, baselines: int, invariant_ok: bool, target_gone: bool,
           new_clusters: list[str], sites_remaining: int,
           metric_status: str) -> str:
    if baselines < 3:
        return "needs_recheck"
    if not invariant_ok or not target_gone:
        return "partial"
    if new_clusters:
        return "partial"
    if sites_remaining:
        return "partial"
    if metric_status == "unmeasured":
        return "needs_recheck"
    return metric_status
