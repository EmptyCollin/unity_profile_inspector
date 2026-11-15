"""I25-ext：RepairEngine 协议与串行实现。

orchestrator 只调用 attempt/rollback/tree，不自己维护 checkpoint。
"""
from __future__ import annotations

from typing import Callable, Protocol

from upi.core.repair_tree import RepairTree, restore_checkpoint


class RepairEngine(Protocol):
    def attempt(self, patch, *, apply, compile_fn, capture, verify,
                rollback, git_head) -> str: ...
    def rollback_to(self, state_id: str) -> dict: ...
    def tree_dict(self) -> dict: ...


class SerialEngine:
    """一次一个 patch 的引擎；保留 partial，失败只回滚当前 patch。"""

    def __init__(self, tree: RepairTree):
        self.tree = tree

    def attempt(self, patch, *, apply: Callable, compile_fn: Callable,
                capture: Callable, verify: Callable, rollback: Callable,
                git_head: Callable[[], str] = lambda: "") -> str:
        if not apply(patch):
            return self.tree.record("infra_failure", reason="apply_failed")
        if not compile_fn():
            rollback(patch)
            return self.tree.record("no_effect", reason="compile_failed")
        obs = capture()
        status = str(verify(obs) or "no_effect")
        action = self.tree.record(
            status, patch_id=str(getattr(patch, "unit_id", "")),
            git_ref=(git_head() if status in
                     ("improved_partial", "verified") else ""))
        if status not in ("improved_partial", "verified"):
            rollback(patch)
        return action

    def rollback_to(self, state_id: str) -> dict:
        return restore_checkpoint(self.tree, state_id)

    def tree_dict(self) -> dict:
        return self.tree.to_dict()
