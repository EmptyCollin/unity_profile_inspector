"""I23：ConfigEditPlan 最小执行器。支持顶层与列表项字段。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_ROOTS = ("ProjectSettings/", "Assets/Settings/")


@dataclass
class ConfigEdit:
    path: str
    field: str
    before: object
    after: object
    section: str = ""
    index: int = 0


def _p(project: Path, rel: str) -> Path:
    return (project / rel).resolve()


def _block(lines: list[str], section: str, index: int):
    """返回 section 列表第 index 项的 [start,end) 行号；找不到返回 None。"""
    sec = re.compile(rf"^\s*{re.escape(section)}:\s*$")
    start = None
    indent = 0
    for i, ln in enumerate(lines):
        m = sec.match(ln)
        if m:
            start = i
            indent = len(ln) - len(ln.lstrip())
            break
    if start is None:
        return None
    item_starts = []
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if not ln.strip():
            continue
        ind = len(ln) - len(ln.lstrip())
        if ind < indent or (ind == indent and not ln.lstrip().startswith("-")):
            break
        if ind >= indent and ln.lstrip().startswith("-"):
            item_starts.append((i, ind))
    if not item_starts:
        return None
    if index < 0 or index >= len(item_starts):
        return None
    s, item_indent = item_starts[index]
    e = len(lines)
    for j in range(s + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        ind = len(ln) - len(ln.lstrip())
        if ind < item_indent or (ind == item_indent and ln.lstrip().startswith("-")):
            e = j
            break
    return s, e


def _set(text: str, e: ConfigEdit) -> str:
    lines = text.splitlines()
    if e.section:
        blk = _block(lines, e.section, e.index)
        if blk is None:
            raise ValueError(f"section 不存在: {e.section}[{e.index}]")
        s, end = blk
        pat = re.compile(rf"^(\s*){re.escape(e.field)}:\s*.*$")
        for i in range(s, end):
            m = pat.match(lines[i])
            if m:
                lines[i] = f"{m.group(1)}{e.field}: {e.after}"
                return "\n".join(lines) + "\n"
        raise ValueError(f"字段不存在: {e.section}[{e.index}].{e.field}")
    pat = re.compile(rf"^(\s*){re.escape(e.field)}:\s*.*$", re.MULTILINE)
    if not pat.search(text):
        raise ValueError(f"字段不存在: {e.field}")
    return pat.sub(lambda m: f"{m.group(1)}{e.field}: {e.after}", text, count=1)


def validate(project: Path | str, edits: list[ConfigEdit]) -> list[str]:
    root = Path(project).resolve()
    errors = []
    for e in edits:
        if not any(e.path.startswith(x) for x in _ALLOWED_ROOTS):
            errors.append(f"不在配置目录: {e.path}")
            continue
        p = _p(root, e.path)
        try:
            p.relative_to(root)
        except ValueError:
            errors.append(f"路径越界: {e.path}")
            continue
        if not p.is_file():
            errors.append(f"文件不存在: {e.path}")
            continue
        try:
            _set(p.read_text(encoding="utf-8", errors="replace"), e)
        except ValueError as ex:
            errors.append(f"{e.path}: {ex}")
    return errors


def apply(project: Path | str, edits: list[ConfigEdit]) -> list[dict]:
    errors = validate(project, edits)
    if errors:
        raise ValueError("; ".join(errors))
    root = Path(project).resolve()
    done = []
    for e in edits:
        fp = _p(root, e.path)
        original = fp.read_text(encoding="utf-8")
        fp.write_text(_set(original, e), encoding="utf-8")
        done.append({"path": e.path, "field": e.field,
                     "before": original})
    return done


def rollback(project: Path | str, done: list[dict]) -> None:
    root = Path(project).resolve()
    for d in reversed(done):
        _p(root, d["path"]).write_text(d["before"], encoding="utf-8")
