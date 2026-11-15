"""I26-ext E2：screen / candidate / final 三层测量。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MeasureProfile:
    name: str
    baselines: int
    after: int
    require_after: int
    can_verify: bool


PROFILES = {
    "screen": MeasureProfile("screen", 1, 1, 1, False),
    "candidate": MeasureProfile("candidate", 3, 1, 1, False),
    "final": MeasureProfile("final", 5, 3, 3, True),
}


def get(name: str) -> MeasureProfile:
    return PROFILES.get((name or "final").strip().lower(), PROFILES["final"])


def after_repeats(name: str, explicit: int | None = None) -> int:
    p = get(name)
    return max(1, explicit) if explicit is not None else p.after


def can_verify(name: str, after_n: int) -> bool:
    p = get(name)
    return p.can_verify and after_n >= p.require_after
