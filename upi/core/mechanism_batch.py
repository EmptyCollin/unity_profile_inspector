"""I19：把 N 个 finding 合并成 M 个机制单元。

同机制、同站点集合的 findings 合并；一个单元覆盖多个 target。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MechanismUnit:
    unit_id: str
    mechanism: str
    targets: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)

    def key(self) -> str:
        return self.mechanism + "|" + "|".join(sorted(self.sites))


def _mechanism(f: dict) -> str:
    # findings 的 mechanism 常是宽泛类别（hot_function），不能当合并键；
    # 优先用候选根因（具体函数/机制），没有才退回 mechanism。
    att = f.get("attribution") or {}
    fac = att.get("factors") if isinstance(att, dict) else {}
    root = str((fac or {}).get("root_cause") or "")
    if root:
        return root
    m = str(f.get("mechanism") or "")
    return m or "unknown"


def _sites(f: dict) -> list[str]:
    out = []
    for a in (f.get("anchors") or []):
        if isinstance(a, dict):
            file = str(a.get("file") or "")
            method = str(a.get("method") or "")
            if file:
                out.append(f"{file}::{method}" if method else file)
        else:
            out.append(str(a))
    return sorted(set(out))


def consolidate(findings: list[dict]) -> list[MechanismUnit]:
    by_key: dict[str, MechanismUnit] = {}
    for f in findings:
        mech = _mechanism(f)
        sites = _sites(f)
        key = mech
        if key not in by_key:
            by_key[key] = MechanismUnit(
                unit_id=f"unit-{len(by_key) + 1:02d}",
                mechanism=mech, sites=sites)
        u = by_key[key]
        fid = str(f.get("finding_id") or "")
        if fid and fid not in u.finding_ids:
            u.finding_ids.append(fid)
            u.targets.append(str((f.get("attribution") or {}).get("factors", {})
                                 .get("root_cause") or fid))
        for s in sites:
            if s not in u.sites:
                u.sites.append(s)
    return list(by_key.values())


@dataclass
class PatchRef:
    unit_id: str
    files: list[str] = field(default_factory=list)


def conflict(groups: list[PatchRef], p: PatchRef) -> bool:
    return any(set(g.files) & set(p.files) for g in groups)


def group_patches(patches: list[PatchRef]) -> list[list[PatchRef]]:
    """同文件补丁不并组；不同文件可同组。"""
    groups: list[list[PatchRef]] = []
    for p in patches:
        for g in groups:
            if not conflict(g, p):
                g.append(p)
                break
        else:
            groups.append([p])
    return groups
