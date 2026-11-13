"""I15：统一验证判定（cluster + 站点完整性 + 稳态）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from upi.core.steady_state import check_steady


@dataclass
class VerificationV2:
    status: str
    target_gone: bool
    new_clusters: list[str] = field(default_factory=list)
    guards_ok: bool = True
    sites_remaining: dict[str, list[str]] = field(default_factory=dict)
    steady_ok: Optional[bool] = None
    pseudo: bool = False
    reason: str = ""


def evaluate_v2(*, target_gone: bool, new_clusters: list[str],
                guards_ok: bool, sites_remaining: dict[str, list[str]] | None = None,
                series: list[float] | None = None, invariant: str = "zero",
                baselines_ok: bool = True) -> VerificationV2:
    sites = sites_remaining or {}
    if not target_gone or new_clusters or not guards_ok:
        return VerificationV2("needs_recheck", target_gone, new_clusters,
                              guards_ok, sites, reason="目标/新 cluster/守护未过")
    if sites:
        return VerificationV2("partial_fix", target_gone, new_clusters,
                              guards_ok, sites, reason="仍有站点未覆盖")
    steady = None
    if series is not None:
        sr = check_steady(series, invariant=invariant)
        steady = sr.ok
        if not sr.ok:
            return VerificationV2("pseudo_fix" if sr.pseudo else "needs_recheck",
                                  target_gone, new_clusters, guards_ok, sites,
                                  steady_ok=False, pseudo=sr.pseudo,
                                  reason=sr.reason)
    if not baselines_ok:
        return VerificationV2("observed", target_gone, new_clusters,
                              guards_ok, sites, steady_ok=steady,
                              reason="基线不足")
    return VerificationV2("verified", target_gone, new_clusters, guards_ok,
                          sites, steady_ok=steady,
                          reason="cluster+站点+守护通过")
