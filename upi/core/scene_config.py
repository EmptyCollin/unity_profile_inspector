"""I23：场景组件配置只读快照（Light/Camera/ReflectionProbe/Volume/Canvas）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_COMPONENT = re.compile(r"^--- !u!(\d+) &(\d+).*$")
_FIELD = re.compile(r"^\s{2}([A-Za-z_]\w*):\s*(.+?)\s*$")
_TYPES = {
    "Light", "Camera", "ReflectionProbe", "Volume", "Canvas",
    "RenderSettings", "OcclusionCullingSettings",
}


@dataclass
class Component:
    type: str
    file_id: str
    fields: dict = field(default_factory=dict)


@dataclass
class SceneConfig:
    path: str
    components: list[Component] = field(default_factory=list)


def scan_scene(path: Path, root: Path) -> SceneConfig:
    comps: list[Component] = []
    cur: Component | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _COMPONENT.match(line)
        if m:
            cur = None
        elif line and not line.startswith(" ") and line.endswith(":"):
            name = line[:-1].strip()
            if name in _TYPES:
                cur = Component(type=name, file_id="")
                comps.append(cur)
        elif cur is not None:
            f = _FIELD.match(line)
            if f:
                cur.fields[f.group(1)] = f.group(2)
    return SceneConfig(path=path.relative_to(root).as_posix(), components=comps)


def scan(project: Path | str) -> list[SceneConfig]:
    root = Path(project).resolve()
    scenes = list((root / "Assets").rglob("*.unity"))
    return [scan_scene(p, root) for p in sorted(scenes)]
