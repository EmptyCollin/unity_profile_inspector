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
        return {"n": 0, "median": None, "sigma": None, "mad": None}
    med = statistics.median(vals)
    mad = statistics.median([abs(x - med) for x in vals])
    return {"n": len(vals), "median": med,
            "sigma": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
            "mad": mad}


def min_ratio_for(metric: str) -> float:
    """不同指标的最小相对改善线；frame_time 比绘制定数更敏感。"""
    m = str(metric or "").lower()
    if m in ("frame_time", "cpu_frame_time", "main_thread"):
        return 0.03
    if m in ("gc_alloc", "gc_alloc_bytes", "alloc_bytes"):
        return 0.10
    if m in ("drawcalls", "draw_calls", "batches"):
        return 0.05
    return 0.05


def relative(before: Iterable[float], after: Iterable[float], *,
             k: float = 3.0, min_ratio: float = 0.05) -> dict:
    b, a = stats(before), stats(after)
    if not b["n"] or not a["n"]:
        return {"status": "unmeasured", "before": b, "after": a,
                "delta": None, "threshold": None}
    delta = b["median"] - a["median"]
    # 用 MAD 的稳健噪声；σ 在窗口内有离群值时会被放大到失效。
    robust = 1.4826 * max(float(b["mad"] or 0.0), float(a["mad"] or 0.0))
    threshold = max(k * robust, abs(b["median"]) * min_ratio, 1e-9)
    if delta < -threshold:
        status = "regressed"
    elif delta > threshold:
        status = "significant"
    else:
        status = "not_significant"
    return {"status": status, "before": b, "after": a,
            "delta": delta, "threshold": threshold, "k": k,
            "noise": robust, "min_ratio": min_ratio}


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
    robust = 1.4826 * max(float(b["mad"] or 0.0), float(a["mad"] or 0.0))
    threshold = max(k * robust, abs(b["median"]) * tolerance, 1e-9)
    return {"status": "drift" if delta > threshold else "ok",
            "delta": delta, "threshold": threshold,
            "before": b, "after": a, "noise": robust}


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


def group_by_overlap(items: list[dict]) -> list[list[dict]]:
    """按 window 相交或补丁文件相交分组；不相交才可独立验收。

    每个 item 至少读 window_ids / files；缺省视为空。
    """
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    wins = [set(str(x) for x in (it.get("window_ids") or []))
            for it in items]
    files = [set(str(x) for x in (it.get("files") or [])) for it in items]
    for i in range(n):
        for j in range(i + 1, n):
            if (wins[i] & wins[j]) or (files[i] & files[j]):
                union(i, j)
    groups: dict[int, list[dict]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return [groups[k] for k in sorted(groups)]


def status_compare(records: list[dict]) -> dict:
    """对比旧 status 与 acceptance.status，供决定是否默认开启三轴。"""
    from collections import Counter
    pairs: Counter = Counter()
    old_counts: Counter = Counter()
    new_counts: Counter = Counter()
    changed = 0
    for r in records or []:
        old = str(r.get("status") or "")
        acc = (r.get("acceptance") or {}).get("status") or ""
        if not old or not acc or acc == "unassessed":
            continue
        pairs[f"{old} -> {acc}"] += 1
        old_counts[old] += 1
        new_counts[acc] += 1
        if old != acc:
            changed += 1
    return {"n": sum(old_counts.values()), "changed": changed,
            "pairs": dict(pairs), "old": dict(old_counts),
            "new": dict(new_counts)}


def merge_window_verdicts(records: list[dict]) -> dict:
    """同窗口多 unit 合并成一个 window verdict。

    unit 的 patch 仍各自 verify，但同一 exact window 的验收结论只能有一个：
    取最差状态（semantic > drift > regressed > not_significant > partial > verified）。
    """
    rank = {
        "verified": 0,
        "improved_partial_absolute_unassessed": 1,
        "improved_partial_absolute_unmet": 2,
        "not_significant": 3,
        "regressed": 4,
        "environment_drift": 5,
        "semantic_risk": 6,
    }
    groups: dict[str, list[dict]] = {}
    for r in records or []:
        w = str(r.get("window") or "")
        if w:
            groups.setdefault(w, []).append(r)
    out: dict[str, dict] = {}
    for w, rows in groups.items():
        statuses = []
        for r in rows:
            st = str((r.get("acceptance") or {}).get("status") or "")
            if not st or st == "unassessed":
                st = str(r.get("status") or "insufficient_evidence")
            statuses.append(st)
        merged = max(statuses, key=lambda s: rank.get(s, 3))
        for r in rows:
            r["window_status"] = merged
        out[w] = {"window": w,
                  "units": [str(r.get("unit_id") or "") for r in rows],
                  "statuses": statuses, "status": merged,
                  "n": len(rows)}
    return out


def verdict(unit_id: str, *, before: Iterable[float], after: Iterable[float],
            control_before: Iterable[float] = (),
            control_after: Iterable[float] = (),
            absolute_target: float | None = None,
            higher_is_better: bool = False,
            semantic_risk: str = "",
            metric: str = "",
            min_ratio: float | None = None) -> Verdict:
    ratio = (min_ratio if min_ratio is not None else min_ratio_for(metric))
    rel = relative(before, after, min_ratio=ratio)
    obs = (rel.get("after") or {}).get("median")
    abs_ = absolute(obs, absolute_target, higher_is_better=higher_is_better)
    env = (environment(control_before, control_after)
           if list(control_before) and list(control_after) else {"status": "unassessed"})
    status = decide(rel=rel, abs_=abs_, env=env, semantic_risk=semantic_risk)
    return Verdict(unit_id=unit_id, relative=rel, absolute=abs_,
                   control=env, semantic={"risk": semantic_risk},
                   status=status)
