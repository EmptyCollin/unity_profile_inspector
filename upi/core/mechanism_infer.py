"""I20：从代码模式推导 metric / 不变量，不依赖项目类名。"""
from __future__ import annotations

import re
from pathlib import Path

PATTERNS: dict[str, str] = {
    "canvas_force_update": r"ForceUpdateCanvases\s*\(",
    "per_instance_material": r"new\s+Material\s*\(",
    "per_tile_texture": r"new\s+Texture2D\s*\(",
    "sync_resources_load": r"Resources\.Load\s*[<(]",
    "per_clear_instantiate": r"new\s+GameObject\s*\(",
    "idle_hint_scan": r"\bHasMatch\s*\(",
    "per_frame_animation": r"\blocalScale\s*=",
    "bool_buffer_alloc": r"new\s+bool\s*\[",
}

METRIC = {
    "per_instance_material": "drawcalls",
    "per_tile_texture": "memory",
    "sync_resources_load": "frame_time",
    "per_clear_instantiate": "gc_alloc",
    "canvas_force_update": "frame_time",
    "idle_hint_scan": "frame_time",
    "per_frame_animation": "frame_time",
    "bool_buffer_alloc": "gc_alloc",
}


def infer_from_text(text: str) -> tuple[str, str] | None:
    for name, pat in PATTERNS.items():
        if re.search(pat, text or ""):
            return name, pat
    return None


def metric_for_text(text: str) -> str:
    hit = infer_from_text(text)
    return METRIC.get(hit[0], "frame_time") if hit else "frame_time"


def diff_text(diffs: list[dict]) -> str:
    return "\n".join(str(d.get("find", "")) for d in (diffs or []))


def method_for_find(project: Path, file: str, find: str) -> str:
    fp = (Path(project) / file).resolve()
    if not fp.is_file():
        return ""
    text = fp.read_text(encoding="utf-8", errors="replace")
    idx = text.find(find)
    if idx < 0:
        return ""
    head = text[:idx]
    starts = list(re.finditer(
        r"\b(?:void|bool|int|float|string|IEnumerator|[A-Z]\w+)"
        r"\s+(\w+)\s*\(", head))
    return starts[-1].group(1) if starts else ""


def method_text(project: Path, file: str, method: str) -> str:
    fp = (Path(project) / file).resolve()
    if not fp.is_file() or not method:
        return ""
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    decl = re.compile(r"\b(?:void|bool|int|float|string|IEnumerator|"
                      r"[A-Z]\w+)\s+" + re.escape(method) + r"\s*\(")
    for i, ln in enumerate(lines):
        if not decl.search(ln):
            continue
        depth = 0
        started = False
        out = []
        for j in range(i, len(lines)):
            out.append(lines[j])
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                    started = True
                elif ch == "}":
                    depth -= 1
            if started and depth <= 0:
                return "\n".join(out)
        return "\n".join(out)
    return ""


def count_in_method(project: Path, file: str, method: str,
                    pattern: str) -> int:
    body = method_text(project, file, method)
    return len(re.findall(pattern, body)) if body else 0
