"""I26-ext E5：patch 形状判定。"""
from __future__ import annotations


def classify(diffs: list[dict]) -> str:
    if not diffs:
        return "empty"
    text_find = "\n".join(str(d.get("find") or "") for d in diffs)
    text_repl = "\n".join(str(d.get("replace") or "") for d in diffs)
    for d in diffs:
        find = str(d.get("find") or "")
        repl = str(d.get("replace") or "")
        if not repl.strip():
            return "semantic_delete"
        if len(find) - len(repl) > 400:
            return "semantic_delete"
    # 新增字段 / 缓存 / 缓冲区 / 新方法 → 结构改动
    if ("private " in text_repl or "Ensure" in text_repl
            or "new " in text_repl or "buffer" in text_repl.lower()
            or "cache" in text_repl.lower()):
        return "structural"
    # 只删一个调用 / 只 hoist 一个表达式 → 微优化
    if text_repl.strip() == "":
        return "semantic_delete"
    if len(text_find) - len(text_repl) > 0 and "return" not in text_repl:
        if text_find.count("\n") >= 2:
            return "micro"
    return "unknown"
