"""I20：从 counters 读取目标 metric 的基线/after 统计。"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Optional

ALIASES = {
    "frame_time": ["CPU Main Thread Frame Time", "frame_time_ms"],
    "cpu_frame_time": ["CPU Main Thread Frame Time", "cpuMainThreadFrameTimeMs"],
    "gc_alloc": ["GC Allocated In Frame", "gc_alloc_per_frame"],
    "memory": ["Total Used Memory", "total_used_memory"],
    "drawcalls": ["Draw Calls Count", "drawCalls"],
    "setpass": ["SetPass Calls Count", "setPassCalls"],
    "triangles": ["Triangles Count", "triangles"],
}


def load_counters(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def metric_series(data: dict, metric: str) -> list[float]:
    counters = data.get("counters") if isinstance(data, dict) else None
    if not isinstance(counters, dict):
        return []
    for name in ALIASES.get(metric, [metric]):
        node = counters.get(name)
        if isinstance(node, dict):
            vals = node.get("series")
            if isinstance(vals, list) and vals:
                return [float(x) for x in vals]
    return []


def stats(values: list[float]) -> Optional[dict]:
    if not values:
        return None
    return {"n": len(values),
            "median": statistics.median(values),
            "last": values[-1],
            "sigma": statistics.pstdev(values) if len(values) > 1 else 0.0}


def baseline_after(before_path: Path, after_path: Path,
                   metric: str) -> dict:
    b = stats(metric_series(load_counters(before_path), metric))
    a = stats(metric_series(load_counters(after_path), metric))
    return {"before": b, "after": a, "metric": metric,
            "delta": (b["median"] - a["median"]) if b and a else None}
