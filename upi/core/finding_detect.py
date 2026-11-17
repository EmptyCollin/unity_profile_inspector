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


def findings_from_capture(
    run_dir: Path | str,
    *,
    baseline_dirs: Iterable[Path | str] = (),
    project_id: str = "",
    metrics: Iterable[str] = DEFAULT_METRICS,
    k: float = 3.0,
    min_ratio: float = 0.15,
    min_abs: dict[str, float] | None = None,
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
    out.sort(key=lambda f: f.deviation_ratio, reverse=True)
    return out
