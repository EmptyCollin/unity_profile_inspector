"""I20：从 findings + counters 构建 TargetSpec 并判定。"""
from __future__ import annotations

from upi.core.target_metrics import metric_series, stats
from upi.core.target_verification import (TargetObservation, TargetSpec,
                                          evaluate_target)


def build_spec(finding: dict, before: dict, *,
               metric: str = "frame_time") -> TargetSpec:
    vals = metric_series(before, metric)
    base = vals[-3:] if vals else []
    return TargetSpec(
        finding_id=str(finding.get("finding_id") or ""),
        mechanism=str(finding.get("mechanism") or ""), metric=metric,
        baseline_values=base,
        min_delta=float(finding.get("min_delta") or 0.0),
        invariant=str(finding.get("invariant") or ""),
    )


def evaluate(finding: dict, before: dict, after: dict, *,
             metric: str = "frame_time",
             invariant_ok: bool = True, sites_remaining: int = 0,
             new_clusters: list[str] | None = None,
             guards_ok: bool = True, pseudo: bool = False):
    spec = build_spec(finding, before, metric=metric)
    vals = metric_series(after, spec.metric)
    obs = TargetObservation(
        after=(stats(vals)["median"] if vals else None),
        invariant_ok=invariant_ok, sites_remaining=sites_remaining,
        new_clusters=new_clusters or [], guards_ok=guards_ok, pseudo=pseudo)
    return spec, obs, evaluate_target(spec, obs)
