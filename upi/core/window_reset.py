"""I27：窗口级 reset + verified 补丁重放。

从 exam_batch 抽出，便于用临时 git 仓做确定性测试：
reset 到组前快照 → 重放 keep 补丁 → 编译；失败再 reset。
"""
from __future__ import annotations

import time
from typing import Callable


def reset_and_replay(
    project,
    group_base: str,
    keep: list[tuple[str, object]],
    *,
    reset: Callable[[object, str], bool],
    apply: Callable[[object], bool],
    compile_fn: Callable[[], bool],
    rollback: Callable[[object], bool],
    reset_retries: int = 3,
) -> dict:
    """返回 {applied, verified, failed, reset_ok, compile_ok, fallback}。"""
    out = {"applied": [], "verified": [], "failed": {},
           "reset_ok": False, "compile_ok": None, "fallback": False}
    if not group_base:
        out["fallback"] = True
        for uid, p in keep:
            rollback(p)
            out["failed"][uid] = "no_group_base"
        return out
    for _ in range(max(1, reset_retries)):
        if reset(project, group_base):
            out["reset_ok"] = True
            break
        time.sleep(0.4)
    if not out["reset_ok"]:
        out["fallback"] = True
        for uid, p in keep:
            rollback(p)
            out["failed"][uid] = "group_reset_failed"
        return out
    for uid, p in keep:
        if not apply(p):
            out["failed"][uid] = "window_reapply_failed"
            continue
        out["applied"].append(uid)
        out["verified"].append(uid)
    if not out["applied"]:
        return out
    out["compile_ok"] = bool(compile_fn())
    if not out["compile_ok"]:
        reset(project, group_base)
        out["failed"].update({u: "window_reapply_compile_failed"
                              for u in out["applied"]})
        out["applied"] = []
        out["verified"] = []
    return out
