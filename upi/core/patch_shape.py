"""I26-ext E5：patch 形状判定。"""
from __future__ import annotations


def _added(d: dict) -> str:
    find = str(d.get("find") or "")
    repl = str(d.get("replace") or "")
    if find and len(find) >= 20 and find in repl:
        return repl.replace(find, "", 1)
    return repl


def classify(diffs: list[dict]) -> str:
    if not diffs:
        return "empty"
    if all(str(d.get("find") or "") == str(d.get("replace") or "")
           for d in diffs):
        return "identity"
    for d in diffs:
        find = str(d.get("find") or "")
        repl = str(d.get("replace") or "")
        if not repl.strip():
            return "semantic_delete"
        if len(find) - len(repl) > 400:
            return "semantic_delete"
    added = "\n".join(_added(d) for d in diffs)
    if ("private " in added or "Ensure" in added
            or "new " in added or "buffer" in added.lower()
            or "cache" in added.lower()):
        return "structural"
    # 只删一个调用 / 只换表达式 → 微优化
    removed = sum(max(0, len(str(d.get("find") or ""))
                      - len(str(d.get("replace") or ""))) for d in diffs)
    if removed > 0 and "return" not in added:
        return "micro"
    return "unknown"
