"""1Hz 轮询哨兵（I1）：只做交叉核对，永远不单独作为 finding 依据。

判据沿用 #30/G16④ 的整束级思路：探针中位数与轮询中位数的比值必须落在
[ratio_min, ratio_max]。两侧同零 = 零对零无信息量，**不判通过**（fail-loud），
避免“装了但没数”被当成合法值。
"""
from __future__ import annotations

from pydantic import BaseModel


class SentinelVerdict(BaseModel):
    ok: bool
    ratio: float | None = None
    probe_median: float
    poll_median: float
    reason: str = ""


def compare(probe_median: float, poll_median: float, *,
            ratio_min: float = 0.2, ratio_max: float = 5.0) -> SentinelVerdict:
    p, q = float(probe_median), float(poll_median)
    if q == 0.0:
        if p == 0.0:
            return SentinelVerdict(ok=False, ratio=None, probe_median=p, poll_median=q,
                                   reason="zero_to_zero_no_info")
        return SentinelVerdict(ok=False, ratio=None, probe_median=p, poll_median=q,
                               reason="poll_zero_probe_nonzero")
    ratio = p / q
    if ratio < ratio_min or ratio > ratio_max:
        return SentinelVerdict(ok=False, ratio=round(ratio, 4),
                               probe_median=p, poll_median=q,
                               reason=f"ratio_out_of_range({ratio_min}~{ratio_max})")
    return SentinelVerdict(ok=True, ratio=round(ratio, 4),
                           probe_median=p, poll_median=q, reason="ok")


def check_pairs(pairs: dict[str, tuple[float, float]], *,
                ratio_min: float = 0.2, ratio_max: float = 5.0) -> dict:
    """{metric: (probe_median, poll_median)} → {metric: verdict}。"""
    return {k: compare(p, q, ratio_min=ratio_min, ratio_max=ratio_max)
            for k, (p, q) in pairs.items()}
