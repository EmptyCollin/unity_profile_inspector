"""I25：逐 finding 串行修复循环（DFS + checkpoint）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from upi.core.repair_engine import SerialEngine
from upi.core.repair_tree import RepairTree


@dataclass
class LoopResult:
    statuses: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    attempts: int = 0
    stopped: str = ""


def run_repair_loop(
    tree: RepairTree,
    *,
    plan: Callable[[object], object],
    apply: Callable[[object], bool],
    compile_fn: Callable[[], bool],
    capture: Callable[[], object],
    verify: Callable[[object, object], str],
    rollback: Callable[[object], bool],
    git_head: Callable[[], str] = lambda: "",
    on_failure: Callable[[RepairTree], None] | None = None,
    max_steps: int = 32,
) -> LoopResult:
    """一次只修一个 finding；keep 写 git_ref，失败只回滚当前 patch。"""
    out = LoopResult()
    engine = SerialEngine(tree)
    for step in range(max_steps):
        if tree.finished:
            out.stopped = tree.reason or "finished"
            break
        patch = plan(tree.current)
        if patch is None:
            action = tree.record("no_effect", reason="no_plan")
            out.actions.append(action)
            out.attempts += 1
            if action in ("finding_failed", "budget_exhausted",
                          "depth_exhausted"):
                out.stopped = action
                break
            continue
        action = engine.attempt(
            patch, apply=apply, compile_fn=compile_fn, capture=capture,
            verify=lambda obs: verify(tree.current, obs),
            rollback=rollback, git_head=git_head)
        out.statuses.append(engine.last_status)
        out.actions.append(action)
        out.attempts += 1
        if tree.finished:
            out.stopped = tree.reason or "finished"
            break
    if tree.root_failed() and on_failure is not None:
        on_failure(tree)
    return out
