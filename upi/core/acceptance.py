"""I27 验收三轴：relative / absolute / environment。

不做采集、不读 paper；只吃窗口 series 与绝对目标，给 Verdict 状态。
"""
from __future__ import annotations

import statistics
from typing import Iterable, Literal

from upi.core.finding import Verdict, VerdictStatus


def stats(series: Iterable[float]) -> dict:
    vals = [float(x) for x in series]
    if not vals:
        return {"n": 0, "median": None, "sigma": None}
    return {"n": len(vals), "median": statistics.median(vals),
            "sigma": statistics.pstdev(vals) if len(vals) > 1 else 0.0}


def relative(before: Iterable[float], after: Iterable[float], *,
             k: float = 3.0, min_ratio: float = 0.05) -> dict:
    b, a = stats(before), stats(after)
    if not b["n"] or not a["n"]:
        return {"status": "unmeasured", "before": b, "after": a,
                "delta": None, "threshold": None}
    delta = b["median"] - a["median"]
    sigma = max(float(b["sigma"] or 0.0), float(a["sigma"] or 0.0), 1e-9)
    threshold = max(k * sigma, abs(b["median"]) * min_ratio, 1e-9)
    if delta < -threshold:
        status = "regressed"
    elif delta > threshold:
        status = "significant"
    else:
        status = "not_significant"
    return {"status": status, "before": b, "after": a,
            "delta": delta, "threshold": threshold, "k": k}


def absolute(observed: float | None, target: float | None, *,
             higher_is_better: bool = False) -> dict:
    if observed is None or target is None:
        return {"status": "unassessed", "observed": observed,
                "target": target}
    ok = observed >= target if higher_is_better else observed <= target
    return {"status": "met" if ok else "unmet",
            "observed": observed, "target": target,
            "higher_is_better": higher_is_better}


def environment(before: Iterable[float], after: Iterable[float], *,
                k: float = 3.0, tolerance: float = 0.01) -> dict:
    """control window 漂移；漂移超限时 target 的 delta 不可归因于补丁。"""
    b, a = stats(before), stats(after)
    if not b["n"] or not a["n"]:
        return {"status": "unassessed", "before": b, "after": a}
    delta = abs(float(b["median"]) - float(a["median"]))
    sigma = max(float(b["sigma"] or 0.0), float(a["sigma"] or 0.0), 1e-9)
    threshold = max(k * sigma, abs(b["median"]) * tolerance, 1e-9)
    return {"status": "drift" if delta > threshold else "ok",
            "delta": delta, "threshold": threshold,
            "before": b, "after": a}


def decide(*, rel: dict, abs_: dict, env: dict,
           semantic_risk: str = "") -> VerdictStatus:
    if semantic_risk:
        return "semantic_risk"
    if env.get("status") == "drift":
        return "environment_drift"
    r = rel.get("status")
    if r == "regressed":
        return "regressed"
    if r in ("unmeasured", None):
        return "insufficient_evidence"
    if r == "not_significant":
        return "not_significant"
    a = abs_.get("status")
    if a == "met":
        return "verified"
    if a == "unassessed":
        return "improved_partial_absolute_unassessed"
    return "improved_partial_absolute_unmet"


def verdict(unit_id: str, *, before: Iterable[float], after: Iterable[float],
            control_before: Iterable[float] = (),
            control_after: Iterable[float] = (),
            absolute_target: float | None = None,
            higher_is_better: bool = False,
            semantic_risk: str = "") -> Verdict:
    rel = relative(before, after)
    obs = (rel.get("after") or {}).get("median")
    abs_ = absolute(obs, absolute_target, higher_is_better=higher_is_better)
    env = (environment(control_before, control_after)
           if list(control_before) and list(control_after) else {"status": "unassessed"})
    status = decide(rel=rel, abs_=abs_, env=env, semantic_risk=semantic_risk)
    return Verdict(unit_id=unit_id, relative=rel, absolute=abs_,
                   control=env, semantic={"risk": semantic_risk},
                   status=status)
