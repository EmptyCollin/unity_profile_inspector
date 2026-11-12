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


class CasePatch(BaseModel):
    file: str
    patch: str


class CaseManifest(BaseModel):
    case_id: str
    overlay: list[str] = Field(default_factory=list)
    patches: list[CasePatch] = Field(default_factory=list)
    replay: str = ""
    oracle: str = ""
    base_hash: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _safe_paths(self):
        if not self.overlay and not self.patches:
            raise ValueError("case 至少要有 overlay 或 patches")
        for p in self.overlay:
            q = Path(p)
            if q.is_absolute() or ".." in q.parts or not p.strip():
                raise ValueError(f"overlay 路径非法：{p!r}")
        for p in self.patches:
            q = Path(p.file)
            if q.is_absolute() or ".." in q.parts or not p.file.strip():
                raise ValueError(f"patch 目标非法：{p.file!r}")
            q = Path(p.patch)
            if q.is_absolute() or ".." in q.parts or not p.patch.strip():
                raise ValueError(f"patch 路径非法：{p.patch!r}")
        return self


class CaseRun(BaseModel):
    case_id: str
    base: str
    workdir: str
    files: list[str] = Field(default_factory=list)
    replay: str = ""
    oracle: str = ""


class CombinedRun(BaseModel):
    case_ids: list[str]
    base: str
    workdir: str
    files: list[str] = Field(default_factory=list)


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


def _apply_patch(out: Path, case_dir: Path, cp: CasePatch) -> None:
    patch = case_dir / cp.patch
    if not patch.is_file():
        raise ValueError(f"patch 文件缺失：{cp.patch}")
    target = out / cp.file
    if not target.is_file():
        raise ValueError(f"patch 目标不存在：{cp.file}")
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    # 极简 unified diff：只接受以 " " / "+" / "-" 组成的 hunk，按精确旧行匹配。
    hunks: list[tuple[list[str], list[str]]] = []
    old: list[str] = []
    new: list[str] = []
    in_hunk = False
    for raw in patch.read_text(encoding="utf-8").splitlines(keepends=True):
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("@@"):
            if in_hunk:
                hunks.append((old, new))
            old, new, in_hunk = [], [], True
            continue
        if not in_hunk:
            continue
        tag, body = raw[:1], raw[1:]
        if tag == " ":
            old.append(body); new.append(body)
        elif tag == "-":
            old.append(body)
        elif tag == "+":
            new.append(body)
        elif tag == "\\":
            continue
        else:
            raise ValueError(f"非法 patch 行：{raw[:80]!r}")
    if in_hunk:
        hunks.append((old, new))
    if not hunks:
        raise ValueError(f"patch 没有 hunk：{cp.patch}")
    for old_lines, new_lines in reversed(hunks):
        if not old_lines:
            raise ValueError(f"空 hunk 不支持：{cp.patch}")
        found = -1
        for i in range(0, len(lines) - len(old_lines) + 1):
            if lines[i:i + len(old_lines)] == old_lines:
                found = i
                break
        if found < 0:
            raise ValueError(f"patch hunk 冲突：{cp.patch}")
        lines[found:found + len(old_lines)] = new_lines
    target.write_text("".join(lines), encoding="utf-8")


def apply_case(base: Path | str, case_dir: Path | str, out: Path | str) -> CaseRun:
    base = Path(base).resolve()
    case_dir = Path(case_dir).resolve()
    out = Path(out).resolve()
    manifest = CaseManifest(**json.loads((case_dir / "manifest.json").read_text(encoding="utf-8")))
    actual = tree_hash(base)
    if manifest.base_hash and actual != manifest.base_hash:
        raise ValueError(f"base 哈希不匹配：期望 {manifest.base_hash}，实际 {actual}")
    overlay_root = case_dir / "overlay"
    if manifest.overlay and not overlay_root.is_dir():
        raise ValueError("缺少 case/overlay 目录")
    for rel in manifest.overlay:
        src = overlay_root / rel
        if not src.is_file():
            raise ValueError(f"overlay 文件缺失：{rel}")
        if src.suffix.lower() in ASSET_SUFFIXES:
            if not (overlay_root / (rel + ".meta")).is_file():
                raise ValueError(f"资产 overlay 必须带 .meta：{rel}.meta")
    for cp in manifest.patches:
        if not (case_dir / cp.patch).is_file():
            raise ValueError(f"patch 文件缺失：{cp.patch}")
        if not (base / cp.file).is_file():
            raise ValueError(f"patch 目标不存在：{cp.file}")
        if cp.file in manifest.overlay:
            raise ValueError(f"同一文件不能既 overlay 又 patch：{cp.file}")
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
    for cp in manifest.patches:
        _apply_patch(out, case_dir, cp)
        if cp.file not in applied:
            applied.append(cp.file)
    return CaseRun(case_id=manifest.case_id, base=str(base), workdir=str(out),
                   files=applied, replay=manifest.replay, oracle=manifest.oracle)


def apply_cases(base: Path | str, case_dirs: list[Path | str],
                out: Path | str) -> CombinedRun:
    """把多个无冲突 case 覆盖到同一临时副本。

    当前契约：不同 case 的 overlay 路径必须完全不相交。
    同一路径的代码组合留给 patch 机制，不能靠整文件替换叠。
    """
    base = Path(base).resolve()
    out = Path(out).resolve()
    manifests = []
    for cd in case_dirs:
        cd = Path(cd).resolve()
        manifests.append((cd, CaseManifest(**json.loads(
            (cd / "manifest.json").read_text(encoding="utf-8")))))
    actual = tree_hash(base)
    owner: dict[str, str] = {}
    patch_owner: dict[str, str] = {}
    for cd, m in manifests:
        if m.base_hash and m.base_hash != actual:
            raise ValueError(f"{m.case_id} base 哈希不匹配")
        for rel in m.overlay:
            if rel in owner:
                raise ValueError(f"overlay 冲突：{rel} 同时属于 {owner[rel]} 和 {m.case_id}")
            if rel in patch_owner:
                raise ValueError(f"overlay/patch 冲突：{rel} 已被 {patch_owner[rel]} patch")
            owner[rel] = m.case_id
            src = cd / "overlay" / rel
            if not src.is_file():
                raise ValueError(f"{m.case_id} overlay 文件缺失：{rel}")
            if src.suffix.lower() in ASSET_SUFFIXES:
                if not (cd / "overlay" / (rel + ".meta")).is_file():
                    raise ValueError(f"{m.case_id} 资产 overlay 缺 .meta：{rel}.meta")
        for cp in m.patches:
            if cp.file in owner:
                raise ValueError(f"patch 目标被 {owner[cp.file]} overlay：{cp.file}")
            patch_owner.setdefault(cp.file, m.case_id)
            if not (cd / cp.patch).is_file():
                raise ValueError(f"{m.case_id} patch 缺失：{cp.patch}")
            if not (base / cp.file).is_file():
                raise ValueError(f"{m.case_id} patch 目标不存在：{cp.file}")
    _copy_base(base, out)
    applied: list[str] = []
    for cd, m in manifests:
        for rel in m.overlay:
            shutil.copy2(cd / "overlay" / rel, out / rel)
            applied.append(rel)
            if (cd / "overlay" / (rel + ".meta")).is_file():
                shutil.copy2(cd / "overlay" / (rel + ".meta"), out / (rel + ".meta"))
                applied.append(rel + ".meta")
        for cp in m.patches:
            _apply_patch(out, cd, cp)
            if cp.file not in applied:
                applied.append(cp.file)
    return CombinedRun(case_ids=[m.case_id for _, m in manifests],
                       base=str(base), workdir=str(out), files=applied)
