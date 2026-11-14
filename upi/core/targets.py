"""I20：从 findings + counters 构建 TargetSpec 并判定。"""
from __future__ import annotations

from upi.core.target_metrics import metric_series, stats
from upi.core.target_verification import (TargetObservation, TargetSpec,
                                          evaluate_target)

METRIC_BY_MECHANISM = {
    "per_instance_material": "drawcalls",
    "per_tile_texture": "memory",
    "sync_resources_load_on_shop": "frame_time",
    "per_clear_particle_instantiate": "gc_alloc",
    "idle_hint_scan_per_frame": "frame_time",
    "per_frame_tile_animation": "frame_time",
    "item_label_rebuild_per_frame": "frame_time",
    "canvas_layout_churn": "frame_time",
    "match_scan_allocation": "gc_alloc",
    "oversized_resource_asset": "memory",
}

_ROOT_METRIC = {
    "ChromaGame.Update": "frame_time",
    "ChromaGame.RefreshTiles": "memory",
    "ChromaGame.UpdateHud": "frame_time",
    "ChromaGame.UpdateBody": "frame_time",
    "ChromaGame.OpenShop": "frame_time",
    "ChromaGame.ResolveAllSync": "gc_alloc",
    "ChromaGame.ResolvePendingWhenIdle": "frame_time",
    "ChromaGame.RefreshItemLabels": "frame_time",
    "BoardModel.FindMatches": "gc_alloc",
    "ChromaGame.BuildBoard": "drawcalls",
    "payload_0.txt": "memory",
}


def metric_for(mechanism: str) -> str:
    if mechanism in METRIC_BY_MECHANISM:
        return METRIC_BY_MECHANISM[mechanism]
    for root, metric in _ROOT_METRIC.items():
        if root in mechanism:
            return metric
    return "frame_time"


def build_spec(finding: dict, before: dict) -> TargetSpec:
    mech = str(finding.get("mechanism") or "")
    metric = metric_for(mech)
    vals = metric_series(before, metric)
    # 用前 3 个窗口/最后 3 个采样做基线
    base = vals[-3:] if vals else []
    return TargetSpec(
        finding_id=str(finding.get("finding_id") or ""),
        mechanism=mech, metric=metric,
        baseline_values=base,
        min_delta=float(finding.get("min_delta") or 0.0),
        invariant=str(finding.get("invariant") or ""),
    )


def evaluate(finding: dict, before: dict, after: dict, *,
             invariant_ok: bool = True, sites_remaining: int = 0,
             new_clusters: list[str] | None = None,
             guards_ok: bool = True, pseudo: bool = False):
    spec = build_spec(finding, before)
    vals = metric_series(after, spec.metric)
    obs = TargetObservation(
        after=(stats(vals)["median"] if vals else None),
        invariant_ok=invariant_ok, sites_remaining=sites_remaining,
        new_clusters=new_clusters or [], guards_ok=guards_ok, pseudo=pseudo)
    return spec, obs, evaluate_target(spec, obs)
