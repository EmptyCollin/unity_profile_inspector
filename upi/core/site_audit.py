"""I15：站点完整性审计（静态）。

用于“抓全”：补丁删掉/替换掉的机制 token 如果仍出现在工程里，
说明还有同类站点没改，不能直接算 verified。
"""
from __future__ import annotations

import re
from pathlib import Path

SKIP_DIRS = {"Library", "Temp", "Logs", "obj", ".git", "UserSettings"}


def _iter_cs(project: Path):
    root = Path(project)
    for p in root.rglob("*.cs"):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield p


def removed_tokens(find: str, replace: str) -> list[str]:
    """find 里有、replace 里没有的标识符/调用 token。"""
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    a = set(ident.findall(find or ""))
    b = set(ident.findall(replace or ""))
    skip = {"using", "var", "new", "return", "if", "for", "int", "float",
            "true", "false", "null", "this", "public", "private", "void"}
    return sorted(t for t in a - b if len(t) > 3 and t not in skip)


def audit_tokens(project: Path, tokens: list[str]) -> dict[str, list[str]]:
    """token → 仍命中文件列表（只扫 Assets 下 .cs）。"""
    out: dict[str, list[str]] = {}
    files = list(_iter_cs(project))
    for t in tokens:
        hits = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(rf"\b{re.escape(t)}\b", text):
                hits.append(str(p.relative_to(project)).replace("\\", "/"))
        if hits:
            out[t] = hits
    return out


def sites_remaining(project: Path, diffs: list[dict]) -> dict[str, list[str]]:
    toks: set[str] = set()
    for d in diffs:
        toks.update(removed_tokens(str(d.get("find", "")),
                                   str(d.get("replace", ""))))
    return audit_tokens(project, sorted(toks))
