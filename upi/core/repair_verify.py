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


def refine_partial(status: str, *, delta: float | None,
                   new_clusters: list[str] | None = None) -> str:
    """有正改善但未达标时保留为 improved_partial。"""
    if status in ("partial", "needs_recheck") and delta is not None \
            and delta > 0 and not (new_clusters or []):
        return "improved_partial"
    return status


def measurement_status(*, window: str, after_n: int,
                       before_n: int = 0) -> str:
    """判断这次测量是否有资格进入 verdict。"""
    if not window:
        return "unmeasured"
    if after_n < 3:
        return "unstable"
    if before_n < 3:
        return "unmeasured"
    return "measured"
