"""多因归因：绝对值 + 相对值 + 组合覆盖。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Contribution:
    candidate_id: str
    abs_cost: float
    share: float
    sigma: float = 0.0
    major: bool = False


def _method(c: Contribution) -> str:
    return str(c.candidate_id).split(":")[-1].split(".")[-1]


def _overlap(a: Contribution, b: Contribution) -> bool:
    ma, mb = _method(a), _method(b)
    return bool(ma and mb and (ma.startswith(mb) or mb.startswith(ma)))


def _disjoint(items: list[Contribution]) -> list[Contribution]:
    """父子函数只保留绝对成本更大的一个，避免 group_share>1。"""
    out: list[Contribution] = []
    for c in sorted(items, key=lambda x: x.abs_cost, reverse=True):
        if any(_overlap(c, x) for x in out):
            continue
        out.append(c)
    return out


def individual_ok(c: Contribution, *, min_abs: float,
                  min_share: float, major_abs: float) -> bool:
    """个体门槛只看绝对量；share 用于排序/组合覆盖，不做单因否决。"""
    return c.abs_cost >= max(min_abs, 3.0 * c.sigma)


def group_decision(items: list[Contribution], *, min_abs: float,
                   min_share: float, major_abs: float,
                   group_share: float) -> dict:
    """返回 eligible / group_share / reasons。"""
    eligible = [c for c in items
                if individual_ok(c, min_abs=min_abs,
                                 min_share=min_share,
                                 major_abs=major_abs)]
    disjoint = _disjoint(eligible)
    covered = min(1.0, sum(max(0.0, c.share) for c in disjoint))
    reasons = []
    if not eligible:
        reasons.append("no_absolute_candidate")
    if covered < group_share:
        reasons.append("group_share_below_min")
    return {"eligible": bool(eligible) and covered >= group_share,
            "group_share": round(covered, 4),
            "candidates": [c.candidate_id for c in disjoint],
            "reasons": reasons}
