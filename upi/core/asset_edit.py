"""I23：AssetEditPlan 最小执行器。只改 meta，不自动触发 Unity 重导入。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ALLOWED = {"maxTextureSize", "isReadable", "mipmaps"}


@dataclass
class AssetEdit:
    asset_path: str
    field: str
    before: object
    after: object


def _meta(project: Path, asset_path: str) -> Path:
    return project / (asset_path + ".meta")


def validate(project: Path | str, edits: list[AssetEdit]) -> list[str]:
    root = Path(project).resolve()
    errors = []
    for e in edits:
        if e.field not in _ALLOWED:
            errors.append(f"不支持字段: {e.field}")
            continue
        p = (root / e.asset_path).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            errors.append(f"路径越界: {e.asset_path}")
            continue
        if not p.is_file():
            errors.append(f"资产不存在: {e.asset_path}")
        if not _meta(root, e.asset_path).is_file():
            errors.append(f"meta 不存在: {e.asset_path}.meta")
    return errors


def _set_field(text: str, field: str, value: object) -> str:
    pat = re.compile(rf"^(\s*){re.escape(field)}:\s*.*$", re.MULTILINE)
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}{field}: {value}", text, count=1)
    # 无该字段：插入 TextureImporter 第一行后
    m = re.search(r"^TextureImporter:\s*$", text, re.MULTILINE)
    if not m:
        raise ValueError("meta 不是 TextureImporter")
    pos = m.end()
    return text[:pos] + f"\n  {field}: {value}" + text[pos:]


def apply(project: Path | str, edits: list[AssetEdit]) -> list[dict]:
    errors = validate(project, edits)
    if errors:
        raise ValueError("; ".join(errors))
    root = Path(project).resolve()
    done = []
    for e in edits:
        mp = _meta(root, e.asset_path)
        original = mp.read_text(encoding="utf-8")
        updated = _set_field(original, e.field, e.after)
        mp.write_text(updated, encoding="utf-8")
        done.append({"asset_path": e.asset_path, "field": e.field,
                     "before": original, "after": updated})
    return done


def rollback(project: Path | str, done: list[dict]) -> None:
    root = Path(project).resolve()
    for d in reversed(done):
        _meta(root, d["asset_path"]).write_text(d["before"], encoding="utf-8")
