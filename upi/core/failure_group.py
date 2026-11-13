"""I19：失败分组与轮次推进。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoundState:
    round_no: int = 1
    active: list[str] = field(default_factory=list)
    history: dict[str, list[str]] = field(default_factory=dict)

    def next_round(self, failed: list[str], max_rounds: int) -> bool:
        if self.round_no >= max_rounds or not failed:
            return False
        for uid in failed:
            self.history.setdefault(uid, []).append(
                f"round{self.round_no}:failed")
        self.active = list(failed)
        self.round_no += 1
        return True


def bisect_groups(ids: list[str]) -> list[list[str]]:
    """失败子集二分：先一半，后另一半；用于定位坏补丁。"""
    if len(ids) <= 1:
        return [list(ids)] if ids else []
    mid = len(ids) // 2
    return [ids[:mid], ids[mid:]]
