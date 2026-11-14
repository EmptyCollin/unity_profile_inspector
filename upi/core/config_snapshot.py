"""I23：项目/场景配置只读快照。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SCALAR = re.compile(r"^\s{2}([A-Za-z_]\w*):\s*([^\{{\}}]+?)\s*$")


@dataclass
class ConfigFile:
    path: str
    fields: dict = field(default_factory=dict)


def scan_file(path: Path, root: Path) -> ConfigFile:
    fields = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _SCALAR.match(line)
            if m:
                fields[m.group(1)] = m.group(2)
    except OSError:
        pass
    return ConfigFile(path=path.relative_to(root).as_posix(), fields=fields)


def scan(project: Path | str) -> list[ConfigFile]:
    root = Path(project).resolve()
    targets = []
    for folder, patterns in (
        (root / "ProjectSettings", ("*.asset", "*.json")),
        (root / "Assets" / "Settings", ("*.asset",)),
    ):
        if not folder.exists():
            continue
        for pat in patterns:
            targets.extend(folder.rglob(pat))
    return [scan_file(p, root) for p in sorted(set(targets))]


def diff(before: list[ConfigFile], after: list[ConfigFile]) -> dict:
    b = {x.path: x.fields for x in before}
    a = {x.path: x.fields for x in after}
    out = {}
    for path in sorted(set(b) | set(a)):
        changed = {k: [b.get(path, {}).get(k), a.get(path, {}).get(k)]
                   for k in set(b.get(path, {})) | set(a.get(path, {}))
                   if b.get(path, {}).get(k) != a.get(path, {}).get(k)}
        if changed:
            out[path] = changed
    return out
