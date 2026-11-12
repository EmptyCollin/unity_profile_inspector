"""I8c：指标/归因区间底座。

- bootstrap 用于同一候选在多个帧/窗口的重复观测；
- Monte Carlo 用于六因子不确定性下的 c_attr 分布；
- 单帧没有分布时返回退化区间，并标 single_frame，不冒充统计结论。
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Callable, Iterable


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = max(0.0, min(1.0, p)) * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(len(sorted_vals) - 1, lo + 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def bootstrap_ci(values: Iterable[float], *, statistic: Callable[[list[float]], float] = statistics.median,
                 n_resamples: int = 1000, alpha: float = 0.2,
                 seed: int = 20261112) -> dict:
    vals = [float(v) for v in values]
    if not vals:
        return {"point": 0.0, "low": 0.0, "high": 0.0,
                "n": 0, "basis": "empty", "interval": [0.0, 0.0]}
    point = float(statistic(vals))
    if len(vals) == 1:
        return {"point": point, "low": point, "high": point,
                "n": 1, "basis": "single_frame", "interval": [point, point]}
    rng = random.Random(seed)
    stats = []
    for _ in range(max(1, n_resamples)):
        sample = [rng.choice(vals) for _ in range(len(vals))]
        stats.append(float(statistic(sample)))
    stats.sort()
    lo = _percentile(stats, alpha / 2)
    hi = _percentile(stats, 1 - alpha / 2)
    return {"point": point, "low": lo, "high": hi, "n": len(vals),
            "basis": "bootstrap", "interval": [lo, hi]}


# 与 upi.core.attribution.WEIGHTS 对齐；区间模块不反向依赖 attribution，避免环。
DEFAULT_WEIGHTS = {
    "w0": -1.5, "proximity": 0.6, "explains_share": 2.2,
    "competitiveness": 0.8, "prior_hit": 1.2,
    "counterfactual": 1.5, "mechanism": 1.0,
}
FACTOR_KEYS = ("proximity", "explains_share", "competitiveness",
               "prior_hit", "counterfactual", "mechanism")


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def monte_carlo_interval(factors: dict[str, float], *,
                         uncertainty: float = 0.1,
                         weights: dict[str, float] | None = None,
                         n: int = 2000, alpha: float = 0.2,
                         seed: int = 20261112) -> dict:
    """六因子 + 权重扰动下的 c_attr 分布。

    这不是严格频率置信区间；输出 basis=monte_carlo，调用方必须如实标注。
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    rng = random.Random(seed)
    vals = []
    for _ in range(max(1, n)):
        logit = w["w0"]
        for k in FACTOR_KEYS:
            base = float(factors.get(k, 0.0))
            # 因子在 [0,1]；围绕判定值做有界扰动。
            v = base + rng.gauss(0.0, uncertainty)
            v = max(0.0, min(1.0, v))
            wk = w[k] * (1.0 + rng.gauss(0.0, uncertainty * 0.5))
            logit += wk * v
        vals.append(_logistic(logit))
    vals.sort()
    point = _logistic(w["w0"] + sum(w[k] * float(factors.get(k, 0.0))
                                    for k in FACTOR_KEYS))
    return {"point": point, "low": _percentile(vals, alpha / 2),
            "high": _percentile(vals, 1 - alpha / 2), "n": n,
            "basis": "monte_carlo", "interval": [_percentile(vals, alpha / 2),
                                                  _percentile(vals, 1 - alpha / 2)]}
