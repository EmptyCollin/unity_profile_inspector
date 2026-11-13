"""I15：因果结构与站点完整性。

OR  互斥备选：修一个应好
AND 多因并存：都修才可能好
ONE 一因多站点：同一机制多处出现，必须全改
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Relation = Literal["or", "and", "one"]
MechanismClass = Literal["necessary_per_frame", "init_only",
                         "event_driven", "cacheable", "unknown"]


@dataclass
class CausalFactor:
    factor_id: str
    mechanism: str
    sites: list[str] = field(default_factory=list)
    mechanism_class: MechanismClass = "unknown"
    expected_invariant: str = ""
    evidence: str = ""


@dataclass
class CausalModel:
    issue_id: str
    relation: Relation = "or"
    factors: list[CausalFactor] = field(default_factory=list)

    def all_sites(self) -> list[str]:
        out: list[str] = []
        for f in self.factors:
            for s in f.sites:
                if s not in out:
                    out.append(s)
        return out

    def sites_remaining(self, fixed_sites: list[str]) -> list[str]:
        fixed = set(fixed_sites)
        return [s for s in self.all_sites() if s not in fixed]


def classify_relation(factors: list[CausalFactor]) -> Relation:
    """保守分类：同一机制多站点=one；独立机制且都有证据=and；否则 or。"""
    if not factors:
        return "or"
    mechs = {f.mechanism for f in factors}
    if len(mechs) == 1 and len(factors[0].sites) > 1:
        return "one"
    if len(factors) >= 2 and all(f.evidence for f in factors):
        return "and"
    return "or"


def mechanism_class_of(text: str) -> MechanismClass:
    t = (text or "").lower()
    if any(k in t for k in ("instantiate", "new ", "alloc", "resources.load",
                            "getcomponent", "tostring", "scan", "per frame",
                            "每帧", "创建", "分配", "扫描")):
        return "init_only" if any(k in t for k in ("init", "once", "初始化",
                                                   "只一次")) else "cacheable"
    if any(k in t for k in ("animation", "lerp", "sample")):
        return "necessary_per_frame"
    return "unknown"
