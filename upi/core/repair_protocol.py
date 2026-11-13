"""I15：修复尝试的关系协议。

只做决策，不执行；执行器按返回值选择回滚/保留/停止。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Relation = Literal["or", "and", "one"]
Action = Literal["stop", "retry_alternative", "add_cofactor",
                 "fix_remaining_sites", "rollback_and_stop"]


@dataclass
class ProtocolState:
    relation: Relation
    attempts: int
    max_attempts: int
    partial: bool = False
    independent: bool = False
    sites_remaining: int = 0
    verified: bool = False


def decide_next(s: ProtocolState) -> Action:
    if s.verified:
        return "stop"
    if s.relation == "one":
        return "fix_remaining_sites" if s.sites_remaining else "stop"
    if s.relation == "and":
        # 已部分改善 + 有独立证据 → 允许叠加下一个因
        if s.partial and s.independent and s.attempts < s.max_attempts:
            return "add_cofactor"
        return "rollback_and_stop" if s.attempts >= s.max_attempts else "retry_alternative"
    # OR：失败回滚，换备选；预算耗尽停止
    if s.attempts >= s.max_attempts:
        return "rollback_and_stop"
    return "retry_alternative"
