"""I20：补丁契约校验（覆盖站点、结构变化、不变量）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PatchMeta:
    unit_id: str
    mechanism: str
    sites_covered: list[str] = field(default_factory=list)
    structural: bool = False
    invariant: str = ""


@dataclass
class ContractVerdict:
    ok: bool
    status: str
    reason: str


def _numeric_only(find: str, replace: str) -> bool:
    norm = lambda s: re.sub(r"\d+", "N", s)  # noqa: E731
    return bool(find) and norm(find) == norm(replace)


def validate(meta: PatchMeta, *, required_sites: list[str],
             mechanism_class: str, find: str, replace: str) -> ContractVerdict:
    missing = [s for s in required_sites if s not in meta.sites_covered]
    if missing:
        return ContractVerdict(False, "partial",
                               f"未覆盖站点 {len(missing)} 个")
    if mechanism_class in ("init_only", "event_driven", "cacheable"):
        if not meta.structural or _numeric_only(find, replace):
            return ContractVerdict(False, "pseudo",
                                   "只改数字/未做结构变化")
    return ContractVerdict(True, "ok", "")
