"""I20：从 counters 读取目标 metric 的基线/after 统计。"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Optional

from upi.core.metric_registry import default_registry


def load_counters(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def metric_series(data: dict, metric: str) -> list[float]:
    return default_registry().series(data, metric)


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
