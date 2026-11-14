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
    # mechanism 形如 hot_function:ChromaGame.UpdateBody / callstack_hot:...
    tail = mechanism.split(":", 1)[-1]
    if tail in ROOT_CHECK:
        return ROOT_CHECK[tail]
    # 兼容 Type.Method 前缀差异，但只接受最长匹配，避免 Update 误吃 UpdateHud
    hits = [k for k in ROOT_CHECK if tail.endswith(k)]
    if hits:
        return ROOT_CHECK[max(hits, key=len)]
    return ""


def invariant_ok(project: Path, mechanism: str,
                 methods: list[str] | None = None) -> tuple[bool, str]:
    name = invariant_for(mechanism)
    if not name:
        return True, "无静态不变量，交给 metric"
    hits = scan(project).by_mechanism().get(name, [])
    if methods:
        wanted = set(methods)
        hits = [h for h in hits if h.method in wanted]
    return len(hits) == 0, f"{name} sites={len(hits)}"
