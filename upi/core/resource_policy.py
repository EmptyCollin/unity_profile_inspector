"""I26：无 importer 大资源的分类（不做危险删除）。"""
from __future__ import annotations

import re
from pathlib import Path

SKIP = {"Library", "Temp", "Logs", "obj", ".git", "UserSettings"}


def _asset_from_unit(unit) -> str:
    refs = [str(x) for x in (getattr(unit, "targets", []) or [])]
    refs += [str(x) for x in (getattr(unit, "sites", []) or [])]
    for r in refs:
        if ":" in r:
            r = r.split(":", 1)[-1]
        if r and "." in r:
            return r
    return ""


def _referenced(project: Path, asset_path: str) -> list[str]:
    name = Path(asset_path).name
    stem = Path(asset_path).stem
    hits = []
    for p in project.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(project)
        if any(x in SKIP for x in rel.parts):
            continue
        if p.suffix.lower() not in (".cs", ".asset", ".prefab", ".unity",
                                    ".json", ".txt", ".yaml", ".yml"):
            continue
        if p == project / asset_path:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name in text or asset_path in text or stem in text:
            hits.append(str(rel).replace("\\", "/"))
    return hits


def classify(project: Path | str, unit) -> dict:
    root = Path(project).resolve()
    rel = _asset_from_unit(unit)
    if not rel:
        return {"status": "no_plan", "reason": "asset_path_missing"}
    p = root / rel
    if not p.is_file():
        return {"status": "no_plan", "reason": "asset_missing"}
    size = p.stat().st_size
    refs = _referenced(root, rel)
    if refs:
        return {"status": "manual_review", "asset": rel, "size": size,
                "reason": "no_importer_and_referenced",
                "references": refs[:6]}
    return {"status": "manual_review", "asset": rel, "size": size,
            "reason": "no_importer_unreferenced_can_delete",
            "references": []}
