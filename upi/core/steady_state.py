"""I15：多帧稳态判定，防“1000→10”伪优化。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SteadyResult:
    ok: bool
    pseudo: bool
    start: float
    end: float
    peak: float
    slope: float
    reason: str = ""
    series: list[float] = field(default_factory=list)


def check_steady(series: list[float], *, invariant: str = "zero",
                 max_end: float = 0.0) -> SteadyResult:
    """series = 连续帧的创建/分配计数。

    invariant=zero：稳态末值必须为 0；只下降不归零 → pseudo。
    invariant=bounded：末值 ≤ max_end 且不随帧增长。
    """
    vals = [float(x) for x in (series or [])]
    if len(vals) < 2:
        return SteadyResult(False, False, 0, 0, 0, 0, "帧数不足", vals)
    start, end, peak = vals[0], vals[-1], max(vals)
    slope = (end - start) / (len(vals) - 1)
    if invariant == "zero":
        if end <= 0:
            return SteadyResult(True, False, start, end, peak, slope, "", vals)
        if end < start:
            return SteadyResult(False, True, start, end, peak, slope,
                                "数量下降但未归零：疑似伪优化", vals)
        return SteadyResult(False, False, start, end, peak, slope,
                            "稳态计数仍非零", vals)
    if end <= max_end and slope <= 0:
        return SteadyResult(True, False, start, end, peak, slope, "", vals)
    return SteadyResult(False, end < start, start, end, peak, slope,
                        "超出上界或仍在增长", vals)


def pseudo_from_patch(find: str, replace: str) -> bool:
    """只改数字的补丁 = 调参式伪优化。"""
    import re
    norm = lambda s: re.sub(r"\d+", "N", s)  # noqa: E731
    return bool(find) and norm(find) == norm(replace)
