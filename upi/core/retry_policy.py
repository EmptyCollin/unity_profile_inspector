"""重试选择策略（I12d）：换假设、停停滞、受预算。"""
from __future__ import annotations

from upi.core.mode_caps import caps


def max_attempts(mode: str) -> int:
    """最多尝试次数；常驻 1，回归取能力表但封顶 3（成本闸）。"""
    if mode == "resident":
        return 1
    return min(caps(mode).max_loop_steps, 3)


def next_untried(hypotheses: list[dict], tried: set[str]):
    for h in hypotheses:
        rc = str(h.get("root_cause") or "")
        if rc and rc not in tried:
            return h
    return None


def is_stagnant(diff_hash: str, seen: set[str]) -> bool:
    return diff_hash in seen
