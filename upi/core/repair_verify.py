"""I24：verified 判定。invariant 只是必要条件。"""
from __future__ import annotations


def decide(*, baselines: int, invariant_ok: bool, target_gone: bool,
           new_clusters: list[str], sites_remaining: int,
           metric_status: str, require_target_gone: bool = True) -> str:
    if baselines < 3:
        return "needs_recheck"
    if require_target_gone and not target_gone:
        return "partial"
    if not invariant_ok:
        return "partial"
    if new_clusters:
        return "partial"
    if sites_remaining:
        return "partial"
    if metric_status == "unmeasured":
        return "needs_recheck"
    return metric_status
