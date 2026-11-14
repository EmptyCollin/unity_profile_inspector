"""I23：ConfigEditPlan 最小执行器。只改已存在的顶层配置字段。"""
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


def _p(project: Path, rel: str) -> Path:
    return (project / rel).resolve()


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
        text = p.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^\s*{re.escape(e.field)}:\s*", text, re.MULTILINE):
            errors.append(f"字段不存在: {e.path}:{e.field}")
    return errors


def _set(text: str, field: str, value: object) -> str:
    pat = re.compile(rf"^(\s*){re.escape(field)}:\s*.*$", re.MULTILINE)
    if not pat.search(text):
        raise ValueError(f"字段不存在: {field}")
    return pat.sub(lambda m: f"{m.group(1)}{field}: {value}", text, count=1)


def apply(project: Path | str, edits: list[ConfigEdit]) -> list[dict]:
    errors = validate(project, edits)
    if errors:
        raise ValueError("; ".join(errors))
    root = Path(project).resolve()
    done = []
    for e in edits:
        fp = _p(root, e.path)
        original = fp.read_text(encoding="utf-8")
        fp.write_text(_set(original, e.field, e.after), encoding="utf-8")
        done.append({"path": e.path, "field": e.field,
                     "before": original})
    return done


def rollback(project: Path | str, done: list[dict]) -> None:
    root = Path(project).resolve()
    for d in reversed(done):
        _p(root, d["path"]).write_text(d["before"], encoding="utf-8")
