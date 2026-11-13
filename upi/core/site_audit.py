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


def removed_patterns(find: str, replace: str) -> list[str]:
    """find 里被删掉的调用/构造模式，避免把泛型标识符当站点。"""
    calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", find or ""))
    calls -= set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", replace or ""))
    news = set(re.findall(r"new\s+([A-Za-z_]\w*)", find or ""))
    news -= set(re.findall(r"new\s+([A-Za-z_]\w*)", replace or ""))
    skip = {"if", "for", "while", "return", "new", "using", "switch", "catch"}
    out = [rf"\b{re.escape(n)}\s*\(" for n in sorted(calls - skip)]
    out += [rf"new\s+{re.escape(n)}\b" for n in sorted(news)]
    return out


def audit_patterns(project: Path, patterns: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    files = list(_iter_cs(project))
    for pat in patterns:
        hits = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(pat, text):
                hits.append(str(p.relative_to(project)).replace("\\", "/"))
        if hits:
            out[pat] = hits
    return out


def removed_tokens(find: str, replace: str) -> list[str]:
    """兼容旧接口：返回模式列表。"""
    return removed_patterns(find, replace)


def audit_tokens(project: Path, tokens: list[str]) -> dict[str, list[str]]:
    return audit_patterns(project, [rf"\b{re.escape(t)}\b" for t in tokens])


def sites_remaining(project: Path, diffs: list[dict]) -> dict[str, list[str]]:
    pats: set[str] = set()
    for d in diffs:
        pats.update(removed_patterns(str(d.get("find", "")),
                                     str(d.get("replace", ""))))
    return audit_patterns(project, sorted(pats))
