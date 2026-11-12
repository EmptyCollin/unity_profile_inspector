"""I7c：静态优化机会扫描（不依赖 clean 基线，不读 oracle）。

只做“代码里存在值得关注的写法”的静态证据；不声称已复现/已测量收益。
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from upi.core.models import Evidence, OpportunityCodeAnchor, OpportunityFinding


@dataclass(frozen=True)
class Pattern:
    id: str
    metric: str
    link: str
    regex: str
    reference_ref: str
    risk: str = "medium"
    tier: str = "semi"


PATTERNS: tuple[Pattern, ...] = (
    Pattern("ui_text_rebuild_per_frame", "cpuMainThreadFrameTimeMs", "cpu",
            r'^\s*roundText\.text\s*=\s*"ROUND "', "HUD 应脏标记，不应每帧重建文本"),
    Pattern("per_instance_material", "drawCalls", "gpu",
            r"new\s+Material\s*\(", "共享材质/MaterialPropertyBlock 优于逐实例材质"),
    Pattern("match_scan_allocation", "gc_alloc_per_frame", "cpu",
            r"var\s+hit\s*=\s*new\s+bool\s*\[\s*N\s*,\s*N\s*\]", "热路径扫描不应每次分配临时缓冲"),
    Pattern("sync_resources_load_on_shop", "frame_time_ms", "loading",
            r"Resources\.Load\s*<\s*TextAsset\s*>", "异步 Addressables 优于同步 Resources.Load"),
    Pattern("per_clear_particle_instantiate", "gc_alloc_per_frame", "cpu",
            r"Destroy\s*\(\s*\w+\s*,\s*0\.25f\s*\)", "消除特效应池化，不应每次 Instantiate/Destroy"),
    Pattern("canvas_layout_churn", "cpuMainThreadFrameTimeMs", "cpu",
            r"Canvas\.ForceUpdateCanvases\s*\(\s*\)", "每帧强制 Canvas rebuild 应避免"),
    Pattern("per_tile_texture", "total_used_memory", "gpu",
            r"new\s+Texture2D\s*\(\s*2\s*,\s*2", "共享图集优于每格独立 Texture2D/Sprite"),
    Pattern("idle_hint_scan_per_frame", "cpuMainThreadFrameTimeMs", "cpu",
            r"if\s*\(\s*model\.HasMatch\s*\(\s*\)\s*\)\s*break;", "空闲提示应缓存/节流，不应每帧全盘扫描"),
    Pattern("per_frame_tile_animation", "cpuMainThreadFrameTimeMs", "anim",
            r"localScale\s*=\s*Vector3\.one\s*\*\s*\(1f\s*\+\s*0\.02f", "逐帧重算格子缩放应由动画系统/脏标记驱动"),
)


FLAG_FOR_PATTERN = {
    "ui_text_rebuild_per_frame": "legacy_ui",
    "per_instance_material": "legacy_material",
    "match_scan_allocation": "legacy_match_alloc",
    "sync_resources_load_on_shop": "sync_shop_load",
    "per_clear_particle_instantiate": "legacy_particle",
    "canvas_layout_churn": "legacy_canvas",
    "per_tile_texture": "legacy_texture",
    "idle_hint_scan_per_frame": "legacy_hint",
    "per_frame_tile_animation": "legacy_anim",
}


def _iter_cs(root: Path) -> Iterable[Path]:
    assets = root / "Assets"
    if not assets.is_dir():
        assets = root
    for p in assets.rglob("*.cs"):
        parts = {x.lower() for x in p.parts}
        if parts & {"library", "temp", "obj", "logs"}:
            continue
        yield p


def scan(project_root: str | Path, *, project_id: str = "",
         scene: str = "", active_flags: set[str] | None = None) -> list[OpportunityFinding]:
    root = Path(project_root).resolve()
    found: list[OpportunityFinding] = []
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for path in _iter_cs(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for pat in PATTERNS:
            if active_flags is not None and FLAG_FOR_PATTERN.get(pat.id) not in active_flags:
                continue
            try:
                rx = re.compile(pat.regex)
            except re.error:
                continue
            for i, line in enumerate(lines, 1):
                if not rx.search(line):
                    continue
                key = f"{project_id}|{rel}|{i}|{pat.id}"
                oid = "op-" + hashlib.sha256(key.encode()).hexdigest()[:12]
                found.append(OpportunityFinding(
                    id=oid, project_id=project_id, scene=scene,
                    metric=pat.metric, reference_kind="static",
                    reference_ref=pat.reference_ref,
                    evidence=[Evidence(tool="static_scan", raw_ref=f"{rel}:{i}",
                                       value=1.0, unit="match", data_tier="C")],
                    code_anchor=OpportunityCodeAnchor(file=rel, line_range=str(i)),
                    pattern_id=pat.id, suggested_playbook="",
                    permission_tier=pat.tier,  # type: ignore[arg-type]
                    confidence=0.55, risk=pat.risk,
                    created_at=now,
                ))
                break  # 同一文件同一模式只报一次
    return found


def save(findings: list[OpportunityFinding], path: str | Path) -> Path:
    from upi.core import events as E
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    E.atomic_write_text(p, "[\n" + ",\n".join(
        f.model_dump_json(indent=2) for f in findings) + "\n]\n")
    return p
