"""病例 overlay runner：干净 base + 同路径带病文件 → 临时工作区。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

SKIP_DIRS = {"Library", "Temp", "Logs", "UserSettings", "obj", "Obj",
             ".git", ".vs", ".idea"}
ASSET_SUFFIXES = {".asset", ".prefab", ".unity", ".mat", ".controller"}


class CaseManifest(BaseModel):
    case_id: str
    overlay: list[str] = Field(min_length=1)
    replay: str = ""
    oracle: str = ""
    base_hash: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _safe_paths(self):
        for p in self.overlay:
            q = Path(p)
            if q.is_absolute() or ".." in q.parts or not p.strip():
                raise ValueError(f"overlay 路径非法：{p!r}")
        return self


class CaseRun(BaseModel):
    case_id: str
    base: str
    workdir: str
    files: list[str] = Field(default_factory=list)
    replay: str = ""
    oracle: str = ""


def tree_hash(root: Path, *, subdirs=("Assets", "ProjectSettings")) -> str:
    h = hashlib.sha256()
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(root)).replace("\\", "/").encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _copy_base(base: Path, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for item in base.iterdir():
        if item.name in SKIP_DIRS:
            continue
        dst = out / item.name
        if item.is_dir():
            shutil.copytree(item, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        else:
            shutil.copy2(item, dst)


def apply_case(base: Path | str, case_dir: Path | str, out: Path | str) -> CaseRun:
    base = Path(base).resolve()
    case_dir = Path(case_dir).resolve()
    out = Path(out).resolve()
    manifest = CaseManifest(**json.loads((case_dir / "manifest.json").read_text(encoding="utf-8")))
    actual = tree_hash(base)
    if manifest.base_hash and actual != manifest.base_hash:
        raise ValueError(f"base 哈希不匹配：期望 {manifest.base_hash}，实际 {actual}")
    overlay_root = case_dir / "overlay"
    if not overlay_root.is_dir():
        raise ValueError("缺少 case/overlay 目录")
    for rel in manifest.overlay:
        src = overlay_root / rel
        if not src.is_file():
            raise ValueError(f"overlay 文件缺失：{rel}")
        if src.suffix.lower() in ASSET_SUFFIXES:
            if not (overlay_root / (rel + ".meta")).is_file():
                raise ValueError(f"资产 overlay 必须带 .meta：{rel}.meta")
    _copy_base(base, out)
    applied = []
    for rel in manifest.overlay:
        src = overlay_root / rel
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        applied.append(rel)
        if src.suffix.lower() in ASSET_SUFFIXES:
            shutil.copy2(overlay_root / (rel + ".meta"), out / (rel + ".meta"))
            applied.append(rel + ".meta")
    return CaseRun(case_id=manifest.case_id, base=str(base), workdir=str(out),
                   files=applied, replay=manifest.replay, oracle=manifest.oracle)
