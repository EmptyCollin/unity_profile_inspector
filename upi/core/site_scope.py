"""I24 P4：按 unit 的全部 file::method 扫描残留站点。"""
from __future__ import annotations

import re
from pathlib import Path

from upi.core.site_audit import _iter_cs, removed_patterns, sites_remaining


def _method_scope(text: str, method: str) -> str:
    if not method:
        return ""
    m = re.search(r"\b\w[\w<>\[\]]*\s+" + re.escape(method) + r"\s*\(", text)
    if not m:
        return ""
    brace = text.find("{", m.end())
    if brace < 0:
        return ""
    depth = 0
    for j in range(brace, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1]
    return text[m.start():]


def sites_remaining_in_scopes(project: Path, diffs: list[dict],
                              scopes: list[str]) -> dict[str, list[str]]:
    allowed = {s.replace("\\", "/") for s in scopes if "::" in s}
    if not allowed:
        return sites_remaining(project, diffs)
    out: dict[str, list[str]] = {}
    files = list(_iter_cs(project))
    by_file: dict[str, list[str]] = {}
    for s in allowed:
        f, _, method = s.partition("::")
        by_file.setdefault(f, []).append(method)
    for d in diffs:
        for pat in removed_patterns(str(d.get("find", "")),
                                    str(d.get("replace", ""))):
            for p in files:
                rel = str(p.relative_to(project)).replace("\\", "/")
                if rel not in by_file:
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for method in by_file[rel]:
                    body = _method_scope(text, method)
                    if body and re.search(pat, body):
                        key = f"{rel}::{method}"
                        out.setdefault(pat, [])
                        if key not in out[pat]:
                            out[pat].append(key)
    return out
