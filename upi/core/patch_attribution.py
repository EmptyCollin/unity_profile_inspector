"""I20：批量补丁的 target 归因。

一次 capture 后，按 target 判：
- verified 保留
- failed 只回滚 owner patch
- 新 cluster / 守护劣化 / co-fix 才进入异常分组
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PatchTargets:
    unit_id: str
    targets: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass
class TargetOutcome:
    target: str
    status: str
    owner: str = ""
    new_cluster: bool = False
    guard_regression: bool = False


@dataclass
class AttributionResult:
    keep: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)
    co_fix: list[str] = field(default_factory=list)
    anomaly: list[str] = field(default_factory=list)
    needs_group_test: bool = False


def attribute(patches: list[PatchTargets],
              outcomes: list[TargetOutcome]) -> AttributionResult:
    owner: dict[str, str] = {}
    for p in patches:
        for t in p.targets:
            owner.setdefault(t, p.unit_id)
    keep: set[str] = set()
    rollback: set[str] = set()
    co_fix: list[str] = []
    anomaly: list[str] = []
    for o in outcomes:
        uid = o.owner or owner.get(o.target, "")
        if o.status == "verified":
            if uid:
                keep.add(uid)
            else:
                co_fix.append(o.target)
            continue
        if uid and uid not in keep:
            rollback.add(uid)
        if o.new_cluster or o.guard_regression:
            anomaly.append(o.target)
    res = AttributionResult(
        keep=sorted(keep),
        rollback=sorted(rollback - keep),
        co_fix=co_fix,
        anomaly=sorted(set(anomaly)),
        needs_group_test=bool(anomaly),
    )
    return res
