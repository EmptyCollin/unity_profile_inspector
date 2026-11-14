"""I23：资源只读盘点。不修改资产，只产报告。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AssetInfo:
    path: str
    kind: str
    bytes: int
    hash: str = ""
    importer: dict = field(default_factory=dict)


_IMPORT_KEYS = {
    "maxTextureSize": r"maxTextureSize:\s*(\d+)",
    "textureCompression": r"textureCompression:\s*(\d+)",
    "mipmaps": r"mipmaps:\s*(\d+)",
    "isReadable": r"isReadable:\s*(\d)",
    "compressionFormat": r"compressionFormat:\s*(\d+)",
    "loadType": r"loadType:\s*(\d+)",
    "preloadAudioData": r"preloadAudioData:\s*(\d)",
    "meshCompression": r"meshCompression:\s*(\d+)",
}


def _importer(meta: Path) -> dict:
    if not meta.is_file():
        return {}
    text = meta.read_text(encoding="utf-8", errors="replace")
    out = {}
    for key, pat in _IMPORT_KEYS.items():
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1))
    return out


def scan(project: Path | str,
         *, hash_assets: bool = False) -> list[AssetInfo]:
    root = Path(project).resolve()
    assets = root / "Assets"
    out: list[AssetInfo] = []
    for p in assets.rglob("*"):
        if not p.is_file() or p.suffix == ".meta":
            continue
        rel = p.relative_to(root).as_posix()
        meta = p.with_name(p.name + ".meta")
        h = ""
        if hash_assets:
            h = hashlib.sha1(p.read_bytes()).hexdigest()
        out.append(AssetInfo(
            path=rel, kind=p.suffix.lower().lstrip("."),
            bytes=p.stat().st_size, hash=h,
            importer=_importer(meta)))
    return sorted(out, key=lambda x: x.bytes, reverse=True)


def duplicates(items: list[AssetInfo]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for a in items:
        if a.hash:
            groups.setdefault(a.hash, []).append(a.path)
    return {k: v for k, v in groups.items() if len(v) > 1}
