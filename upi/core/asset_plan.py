"""I23：资源编辑候选（只提议，不自动应用）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from upi.core.asset_inventory import scan


@dataclass
class AssetEditProposal:
    asset_path: str
    importer_type: str
    field: str
    before: object
    after: object
    reason: str
    perception_risk: str = "semi"
    expected_effect: str = "memory"
    metadata: dict = field(default_factory=dict)


def propose(project: Path | str, *,
            max_texture_size: int = 2048) -> list[AssetEditProposal]:
    out: list[AssetEditProposal] = []
    for a in scan(project):
        imp = a.importer
        if not imp:
            continue
        size = imp.get("maxTextureSize")
        if size is not None and int(size) > max_texture_size:
            out.append(AssetEditProposal(
                asset_path=a.path, importer_type="TextureImporter",
                field="maxTextureSize", before=int(size),
                after=max_texture_size,
                reason=f"maxTextureSize {size} > {max_texture_size}; "
                       "降低峰值显存",
                expected_effect="memory"))
        readable = imp.get("isReadable")
        if readable == 1:
            out.append(AssetEditProposal(
                asset_path=a.path, importer_type="TextureImporter",
                field="isReadable", before=1, after=0,
                reason="isReadable=1 会让纹理保留 CPU 副本",
                expected_effect="memory"))
        mip = imp.get("mipmaps")
        if mip == 1 and "UI" in a.path:
            out.append(AssetEditProposal(
                asset_path=a.path, importer_type="TextureImporter",
                field="mipmaps", before=1, after=0,
                reason="UI 纹理通常不需要 mipmap",
                expected_effect="memory"))
    return out
