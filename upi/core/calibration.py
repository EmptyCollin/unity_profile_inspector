"""I8c 评测侧校准：评估者用，不进入 Agent 输入。

输入是“预测 + 真实根因/结果”，输出可靠性表、ECE、区间覆盖率、Brier。
没有真实标签时不能调用；本模块不生成标签。
"""
from __future__ import annotations

from typing import Iterable


def _clip(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def reliability_table(preds: Iterable[float], outcomes: Iterable[int],
                      bins: int = 10) -> list[dict]:
    p = [_clip(x) for x in preds]
    y = [1 if int(v) else 0 for v in outcomes]
    if len(p) != len(y):
        raise ValueError("preds/outcomes 长度不一致")
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, v in enumerate(p)
               if (lo <= v < hi) or (b == bins - 1 and v == 1.0)]
        if not idx:
            continue
        rows.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": len(idx),
            "confidence": sum(p[i] for i in idx) / len(idx),
            "accuracy": sum(y[i] for i in idx) / len(idx),
        })
    return rows


def ece(preds: Iterable[float], outcomes: Iterable[int], bins: int = 10) -> float:
    p = [_clip(x) for x in preds]
    y = [1 if int(v) else 0 for v in outcomes]
    if len(p) != len(y) or not p:
        return 0.0
    total, acc = 0.0, 0.0
    for row in reliability_table(p, y, bins):
        total += row["n"]
        acc += row["n"] * abs(row["confidence"] - row["accuracy"])
    return acc / total if total else 0.0


def brier(preds: Iterable[float], outcomes: Iterable[int]) -> float:
    p = [_clip(x) for x in preds]
    y = [1 if int(v) else 0 for v in outcomes]
    if len(p) != len(y) or not p:
        return 0.0
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(p)


def interval_coverage(intervals: Iterable[tuple[float, float]],
                      truths: Iterable[float]) -> dict:
    iv = list(intervals)
    tr = [float(t) for t in truths]
    if len(iv) != len(tr):
        raise ValueError("intervals/truths 长度不一致")
    if not iv:
        return {"n": 0, "covered": 0, "coverage": 0.0}
    hit = sum(1 for (a, b), t in zip(iv, tr) if float(a) <= t <= float(b))
    return {"n": len(iv), "covered": hit, "coverage": hit / len(iv)}
