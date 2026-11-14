"""I20：按 planner 声明的 file/method/pattern 检查站点，不再维护模式表。"""
from __future__ import annotations

import re
from pathlib import Path


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
