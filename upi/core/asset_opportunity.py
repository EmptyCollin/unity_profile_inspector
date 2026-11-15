"""I26-ext E3：资产机会（opportunity）分账。"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class AssetOpportunity:
    asset_path: str
    size_bytes: int
    reason: str = "oversized_resource_asset"
    status: str = "manual_review"
    references: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def scan(project: Path | str, *, min_bytes: int = 128 * 1024) -> list[AssetOpportunity]:
    root = Path(project)
    res = root / "Assets" / "Resources"
    out: list[AssetOpportunity] = []
    if not res.is_dir():
        return out
    for p in sorted(res.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (
                ".txt", ".bytes", ".json", ".asset"):
            continue
        size = p.stat().st_size
        if size <= min_bytes:
            continue
        out.append(AssetOpportunity(
            asset_path=p.relative_to(root).as_posix(), size_bytes=size))
    return out
