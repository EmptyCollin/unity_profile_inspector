"""I17：两级修复循环的决策层。

内层不换目标；根据观测选择：
  refine_same_root / alternative_patch / fix_remaining_sites /
  request_evidence / rollback_and_next / stop
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Action = Literal["verified", "refine_same_root", "alternative_patch",
                 "fix_remaining_sites", "request_evidence",
                 "rollback_and_next", "stop"]


@dataclass
class Observation:
    verdict: str
    target_gone: bool = False
    metric_delta: Optional[float] = None
    new_clusters: list[str] = field(default_factory=list)
    sites_remaining: int = 0
    reason: str = ""
    diff_hash: str = ""


def decide_next(obs: Observation, *, attempts: int, max_attempts: int,
                seen_hashes: set[str] | None = None) -> Action:
    seen = seen_hashes or set()
    if obs.verdict == "verified":
        return "verified"
    if "evidence" in (obs.reason or "").lower() or "no_source" in obs.reason:
        return "request_evidence"
    if obs.sites_remaining > 0:
        return "fix_remaining_sites"
    if obs.diff_hash and obs.diff_hash in seen:
        return "alternative_patch"
    if attempts >= max_attempts:
        return "rollback_and_next"
    if obs.verdict in ("needs_recheck", "pseudo_fix", "partial_fix"):
        # 有改善但目标仍在：优先同根因换方案；无改善也换方案，不换题
        return "refine_same_root" if obs.metric_delta else "alternative_patch"
    return "stop"
