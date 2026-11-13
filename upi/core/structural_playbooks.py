"""I16：机制级结构回退（LLM 判 insufficient 时的安全变换）。

只做可静态验证的结构修复，不按 case/oracle 分支。
"""
from __future__ import annotations

import re
from pathlib import Path


def _method_text(project: Path, anchor: str) -> tuple[str, str]:
    f, _, rest = anchor.partition("::")
    fp = (project / f).resolve()
    if not fp.is_file():
        return f, ""
    text = fp.read_text(encoding="utf-8", errors="replace")
    m = rest.split(".")[-1] if rest else ""
    if not m:
        return f, ""
    lines = text.splitlines()
    decl = re.compile(
        r"\b(?:void|bool|int|float|string|IEnumerator|"
        r"bool\s*\[\s*,\s*\]|[\w<>]+\s*\[\s*\])\s+"
        + re.escape(m) + r"\s*\(")
    for i, ln in enumerate(lines):
        if decl.search(ln):
            return f, "\n".join(lines[i:i + 80])
    return f, ""


def _edit(file: str, find: str, replace: str) -> dict:
    return {"file": file, "find": find, "replace": replace,
            "expect_count": 1, "_why": "structural playbook"}


def propose(project: Path | str, anchors: list[str]) -> list[dict]:
    project = Path(project)
    edits: list[dict] = []
    for a in anchors or []:
        f, text = _method_text(project, a)
        if not text:
            continue
        # 每帧创建临时 Texture2D，仅用于 Apply/width 自检 → 删除
        mm = re.search(
            r"(?P<indent>[ \t]*)var\s+(?P<v>\w+)\s*=\s*new\s+Texture2D\([^;]*\);\s*\n"
            r"[ \t]*(?P=v)\.Apply\(\);\s*\n"
            r"[ \t]*if\s*\(\s*(?P=v)\.width\s*<\s*0\s*\)\s*return;\s*\n",
            text)
        if mm:
            edits.append(_edit(f, mm.group(0), mm.group("indent") + "\n"))
            continue
        # 每帧强制刷新画布 → 删除该行
        mm = re.search(r"(?P<line>[ \t]*using\s*\([^)]*\)\s*)?"
                       r"Canvas\.ForceUpdateCanvases\(\);\s*\n", text)
        if mm:
            edits.append(_edit(f, mm.group(0), ""))
            continue
        # 每 tile new Material → 不再克隆材质
        mm = re.search(r"img\.material\s*=\s*new\s+Material\(img\.material\);\s*\n",
                       text)
        if mm:
            edits.append(_edit(f, mm.group(0), "img.material = img.material;\n"))
            continue
    return edits


def to_plan(edits: list[dict]):
    from upi.core.models import EditPlan
    return EditPlan(playbook_id="PB-STRUCT",
                    diffs=edits,
                    invariants_assert=["结构回退：静态可验证",
                                       "find 精确命中一次"],
                    side_effect="unknown", permission_tier="semi",
                    touched_assets=sorted({e["file"] for e in edits}))
