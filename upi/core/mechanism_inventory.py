"""I20：静态机制站点清单（补运行时候选漏检）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP = {"Library", "Temp", "Logs", "obj", ".git", "UserSettings"}

PATTERNS: dict[str, re.Pattern] = {
    "per_instance_material": re.compile(r"new\s+Material\s*\("),
    "per_tile_texture": re.compile(r"new\s+Texture2D\s*\("),
    "sync_resources_load": re.compile(r"Resources\.Load\s*[<(]"),
    "per_clear_instantiate": re.compile(r"new\s+GameObject\s*\("),
    "per_frame_instantiate": re.compile(r"\bInstantiate\s*\("),
    "idle_hint_scan": re.compile(r"\bHasMatch\s*\("),
    "per_frame_animation": re.compile(r"\blocalScale\s*="),
    "canvas_force_update": re.compile(r"ForceUpdateCanvases\s*\("),
    "bool_buffer_alloc": re.compile(r"new\s+bool\s*\["),
}


@dataclass
class Site:
    mechanism: str
    file: str
    line: int
    method: str = ""


@dataclass
class Inventory:
    sites: list[Site] = field(default_factory=list)

    def by_mechanism(self) -> dict[str, list[Site]]:
        out: dict[str, list[Site]] = {}
        for s in self.sites:
            out.setdefault(s.mechanism, []).append(s)
        return out


def _iter_cs(project: Path):
    root = Path(project)
    for p in root.rglob("*.cs"):
        rel = p.relative_to(root)
        if any(x in SKIP for x in rel.parts):
            continue
        yield p, rel


def _method_at(lines: list[str], idx: int) -> str:
    decl = re.compile(
        r"\b(?:void|bool|int|float|string|IEnumerator|[A-Z]\w+)\s+(\w+)\s*\(")
    for j in range(idx, max(-1, idx - 200), -1):
        m = decl.search(lines[j])
        if m:
            return m.group(1)
    return ""


def scan(project: Path) -> Inventory:
    inv = Inventory()
    for p, rel in _iter_cs(project):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines):
            for mech, pat in PATTERNS.items():
                if pat.search(ln):
                    inv.sites.append(Site(mech, str(rel).replace("\\", "/"),
                                          i + 1, _method_at(lines, i)))
    return inv
