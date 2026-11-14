"""I20：机制不变量检查（静态站点清零）。"""
from __future__ import annotations

from pathlib import Path

from upi.core.mechanism_inventory import scan

ROOT_CHECK = {
    "ChromaGame.Update": "canvas_force_update",
    "ChromaGame.BuildBoard": "per_instance_material",
    "ChromaGame.RefreshTiles": "per_tile_texture",
    "ChromaGame.OpenShop": "sync_resources_load",
    "ChromaGame.ResolveAllSync": "per_clear_instantiate",
    "ChromaGame.ResolvePendingWhenIdle": "idle_hint_scan",
    "ChromaGame.UpdateBody": "per_frame_animation",
    "BoardModel.FindMatches": "bool_buffer_alloc",
}


def invariant_for(mechanism: str) -> str:
    for root, mech in ROOT_CHECK.items():
        if root in mechanism:
            return mech
    return ""


def invariant_ok(project: Path, mechanism: str) -> tuple[bool, str]:
    name = invariant_for(mechanism)
    if not name:
        return True, "无静态不变量，交给 metric"
    hits = scan(project).by_mechanism().get(name, [])
    return len(hits) == 0, f"{name} sites={len(hits)}"
