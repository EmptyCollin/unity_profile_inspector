"""I26：资产候选 → AssetEdit 执行。"""
from __future__ import annotations

from pathlib import Path

from upi.core.asset_edit import AssetEdit, apply_and_reimport, rollback
from upi.core.asset_plan import AssetEditProposal, propose


def propose_for(project: Path | str,
                asset_path: str) -> AssetEditProposal | None:
    for p in propose(project):
        if p.asset_path == asset_path:
            return p
    return None


def propose_for_unit(project: Path | str, unit) -> AssetEditProposal | None:
    """从 exam unit 的 targets/sites 里找可执行的资产 proposal。"""
    refs = [str(x) for x in (getattr(unit, "targets", []) or [])]
    refs += [str(x) for x in (getattr(unit, "sites", []) or [])]
    for p in propose(project):
        for r in refs:
            if p.asset_path in r:
                return p
    return None


def to_edit(p: AssetEditProposal) -> AssetEdit:
    return AssetEdit(p.asset_path, p.field, p.before, p.after)


def apply_candidate(project: Path | str, p: AssetEditProposal,
                    eval_fn=None) -> list[dict]:
    return apply_and_reimport(project, [to_edit(p)], eval_fn=eval_fn)


def rollback_candidate(project: Path | str, done: list[dict]) -> None:
    rollback(project, done)
