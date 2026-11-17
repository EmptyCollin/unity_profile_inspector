"""I27：从 action window counters 直接产 Finding（不含 root cause）。

detect 只回答：
- 哪个精确窗口（cmd-10-open_shop）
- 哪个指标
- 相对参考越线多少
- 证据在哪

根因交给 diagnose；本模块不读 candidate、不读代码、不猜机制。
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Iterable

from upi.core.finding import Finding, WindowRef

METRIC_SPECS: dict[str, tuple[str, str, float]] = {
    # counter 名 -> (短名, 单位, 默认最小绝对差)
    "CPU Main Thread Frame Time": ("frame_time", "ns", 0.05 * 1e6),
    "GC Allocated In Frame": ("gc_alloc", "bytes", 64 * 1024),
    "Draw Calls Count": ("drawcalls", "count", 2.0),
    "Total Used Memory": ("memory", "bytes", 1 * 1024 * 1024),
}

DEFAULT_METRICS = tuple(METRIC_SPECS)

# detect 的绝对门限：这些是“症状线”，不是根因/修复判据。
ABSOLUTE_MIN = {
    "gc_alloc_bytes": 64 * 1024.0,   # 单窗分配
    "function_self_ms": 0.4,          # 单窗游戏函数 self 时间
}


def _load_windows(run_dir: Path) -> list[dict]:
    p = Path(run_dir) / "action_windows.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [w for w in rows if isinstance(w, dict) and w.get("window_id")]


def _series(window: dict, metric: str) -> list[float]:
    node = (window.get("counters") or {}).get(metric) or {}
    series = node.get("series") or []
    if series:
        return [float(x) for x in series]
    if node.get("median") is not None:
        return [float(node["median"])]
    return []


def _median(values: Iterable[float]) -> float:
    vals = list(values)
    return float(statistics.median(vals)) if vals else 0.0


def _mad(values: Iterable[float]) -> float:
    vals = list(values)
    if len(vals) < 2:
        return 0.0
    med = _median(vals)
    return float(statistics.median([abs(v - med) for v in vals]))


def _slug(metric: str) -> str:
    return METRIC_SPECS.get(metric, (metric, "", 0.0))[0]


def _load_probe(run_dir: Path) -> dict:
    p = Path(run_dir) / "unified_probe.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _window_aggregates(run_dir: Path, windows: list[dict]) -> dict[str, dict]:
    """按窗口聚合 allocation bytes 与游戏函数 self_ms（不产 root cause）。"""
    probe = _load_probe(run_dir)
    if not probe:
        return {}
    out: dict[str, dict] = {}
    for w in windows:
        wid = str(w.get("window_id") or "")
        try:
            lo = int(w.get("frame_start") or 0)
            hi = int(w.get("frame_end") or 0)
        except (TypeError, ValueError):
            continue
        alloc_bytes = 0
        top_alloc = ""
        for a in probe.get("allocations") or []:
            try:
                f0 = int(a.get("first_frame") or 0)
            except (TypeError, ValueError):
                continue
            if lo <= f0 <= hi:
                b = int(a.get("bytes") or 0)
                alloc_bytes += b
                if b:
                    top_alloc = str(a.get("path") or "").split(">")[-2:]
        self_ms = 0.0
        top_func = ""
        for f in probe.get("functions") or []:
            sym = str(f.get("symbol") or "")
            if "ChromaGame" not in sym and "BoardModel" not in sym \
                    and "DevSim" not in sym:
                continue
            try:
                fr = int(f.get("frame_idx") or -1)
            except (TypeError, ValueError):
                continue
            if lo <= fr <= hi:
                v = float(f.get("self_ms") or 0.0)
                self_ms += v
                if v > 0 and not top_func:
                    top_func = sym
        out[wid] = {"gc_alloc_bytes": float(alloc_bytes),
                    "function_self_ms": self_ms,
                    "top_alloc": top_alloc, "top_func": top_func}
    return out


def _finding(window: WindowRef, *, run_id: str, metric: str, unit: str,
             observed: dict, reference: dict, magnitude: float,
             ratio: float, ref_path: Path, project_id: str = "") -> Finding:
    return Finding(
        finding_id=f"F-{run_id}-{window.window_id}-{metric}",
        run_id=run_id, project_id=project_id, window=window, metric=metric,
        unit=unit, observed=observed, reference=reference, magnitude=magnitude,
        deviation_ratio=ratio, data_tier="A2",
        evidence_refs=[str(ref_path)], state="discovered")


def findings_from_capture(
    run_dir: Path | str,
    *,
    baseline_dirs: Iterable[Path | str] = (),
    project_id: str = "",
    metrics: Iterable[str] = DEFAULT_METRICS,
    k: float = 3.0,
    min_ratio: float = 0.15,
    min_abs: dict[str, float] | None = None,
    include_evidence: bool = True,
    absolute_min: dict[str, float] | None = None,
) -> list[Finding]:
    """当前 capture vs baseline captures（同 window_id/metric）产 Finding。

    baseline_dirs 为空时，用本次所有窗口的中位数作为参考（只用于单次冒烟，
    不能替代 3 次 baseline）。
    """
    run = Path(run_dir)
    current = _load_windows(run)
    if not current:
        return []
    baselines = [_load_windows(Path(d)) for d in baseline_dirs]
    min_abs = min_abs or {}
    abs_min = {**ABSOLUTE_MIN, **(absolute_min or {})}
    refs: dict[tuple[str, str], list[float]] = {}
    for b in baselines:
        for w in b:
            wid = str(w.get("window_id"))
            for metric in metrics:
                vals = _series(w, metric)
                if vals:
                    refs.setdefault((wid, metric), []).append(_median(vals))
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for w in current:
        wid = str(w.get("window_id"))
        try:
            ref_window = WindowRef.from_id(
                wid, frame_start=int(w.get("frame_start") or 0),
                frame_end=int(w.get("frame_end") or 0))
        except ValueError:
            continue
        for metric in metrics:
            vals = _series(w, metric)
            if not vals:
                continue
            key = (wid, metric)
            if key in seen:
                continue
            cur = _median(vals)
            if baselines:
                base_vals = refs.get(key) or []
                if len(base_vals) < 2:
                    continue
                ref = _median(base_vals)
                noise = max(1.4826 * _mad(base_vals), abs(ref) * 0.02)
                source = "baseline_median"
                base_n = len(base_vals)
            else:
                all_med = [_median(_series(x, metric)) for x in current
                           if _series(x, metric)]
                if len(all_med) < 3:
                    continue
                ref = _median(all_med)
                noise = max(1.4826 * _mad(all_med), abs(ref) * 0.02)
                source = "window_median"
                base_n = len(all_med)
            delta = cur - ref
            ratio = delta / abs(ref) if ref else 0.0
            floor = float(min_abs.get(metric, METRIC_SPECS.get(metric, ("", "", 0.0))[2]))
            if delta <= 0 or delta < k * noise or ratio < min_ratio or delta < floor:
                continue
            seen.add(key)
            out.append(Finding(
                finding_id=f"F-{run.name}-{wid}-{_slug(metric)}",
                run_id=run.name, project_id=project_id, window=ref_window,
                metric=metric, unit=METRIC_SPECS.get(metric, ("", "", 0.0))[1],
                observed={"median": cur, "max": max(vals), "n": len(vals),
                          "series_ref": str(run / "action_windows.json")},
                reference={"median": ref, "noise": noise, "n": base_n,
                           "source": source},
                magnitude=delta, deviation_ratio=ratio, data_tier="A2",
                evidence_refs=[str(run / "action_windows.json")],
                state="discovered"))
    if include_evidence:
        cur_aggs = _window_aggregates(run, current)
        base_aggs = [_window_aggregates(Path(d), _load_windows(Path(d)))
                     for d in baseline_dirs]
        base_refs: dict[tuple[str, str], list[float]] = {}
        for ba in base_aggs:
            for wid, vals in ba.items():
                for metric in ("gc_alloc_bytes", "function_self_ms"):
                    base_refs.setdefault((wid, metric), []).append(
                        float(vals.get(metric) or 0.0))
        for w in current:
            wid = str(w.get("window_id") or "")
            agg = cur_aggs.get(wid)
            if not agg:
                continue
            try:
                ref_window = WindowRef.from_id(
                    wid, frame_start=int(w.get("frame_start") or 0),
                    frame_end=int(w.get("frame_end") or 0))
            except ValueError:
                continue
            for metric, unit in (("gc_alloc_bytes", "bytes"),
                                 ("function_self_ms", "ms")):
                key = (wid, metric)
                if key in seen:
                    continue
                cur = float(agg.get(metric) or 0.0)
                target = float(abs_min.get(metric) or 0.0)
                if target <= 0 or cur < target:
                    continue
                seen.add(key)
                base_vals = base_refs.get(key) or []
                top = agg.get("top_alloc") if metric == "gc_alloc_bytes" \
                    else agg.get("top_func")
                out.append(_finding(
                    ref_window, run_id=run.name, metric=metric, unit=unit,
                    observed={"value": cur, "top": top,
                              "ref": str(run / "unified_probe.json")},
                    reference={"target": target, "source": "absolute_min",
                               "baseline_median": _median(base_vals)
                               if base_vals else None,
                               "baseline_n": len(base_vals)},
                    magnitude=cur - target,
                    ratio=(cur / target - 1.0) if target else 0.0,
                    ref_path=run / "unified_probe.json",
                    project_id=project_id))
    out.sort(key=lambda f: f.deviation_ratio, reverse=True)
    return out
