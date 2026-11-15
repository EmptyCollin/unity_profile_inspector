"""I25-ext：RepairEngine 协议与串行实现。

orchestrator 只调用 attempt/rollback/tree，不自己维护 checkpoint。
"""
from __future__ import annotations

from typing import Callable, Protocol

from upi.core.repair_tree import Budget, RepairTree, restore_checkpoint


class RepairEngine(Protocol):
    def attempt(self, patch, *, apply, compile_fn, capture, verify,
                rollback, git_head) -> str: ...
    def rollback_to(self, state_id: str) -> dict: ...
    def tree_dict(self) -> dict: ...


class SerialEngine:
    """一次一个 patch 的引擎；保留 partial，失败只回滚当前 patch。"""

    def __init__(self, tree: RepairTree):
        self.tree = tree
        self.last_status = ""

    def attempt(self, patch, *, apply: Callable, compile_fn: Callable,
                capture: Callable, verify: Callable, rollback: Callable,
                git_head: Callable[[], str] = lambda: "") -> str:
        if not apply(patch):
            self.last_status = "infra_failure"
            return self.tree.record("infra_failure", reason="apply_failed")
        if not compile_fn():
            rollback(patch)
            self.last_status = "no_effect"
            return self.tree.record("no_effect", reason="compile_failed")
        obs = capture()
        status = str(verify(obs) or "no_effect")
        self.last_status = status
        action = self.tree.record(
            status, patch_id=str(getattr(patch, "unit_id", "")),
            git_ref=(git_head() if status in
                     ("improved_partial", "verified") else ""))
        if status not in ("improved_partial", "verified"):
            rollback(patch)
        return action

    def rollback_to(self, state_id: str) -> dict:
        return restore_checkpoint(self.tree, state_id)

    def record_verdict(self, status: str, *, patch_id: str = "",
                       git_ref: str = "", improved: bool = True) -> str:
        mapped = {"resolved": "verified", "regressed": "regressed",
                  "not_significant": ("improved_partial" if improved
                                      else "no_effect"),
                  "insufficient": "no_effect"}.get(status, status)
        return self.tree.record(mapped, patch_id=patch_id, git_ref=git_ref)

    def tree_dict(self) -> dict:
        return self.tree.to_dict()


def tree_for_run_state(state: dict) -> RepairTree:
    """把 orchestrator 的剩余预算映射成单 finding 子预算。"""
    b = state.get("budget") or {}
    spent = state.get("spent") or {}
    total = max(0, int(b.get("max_attempts_per_issue", 3)))
    used = max(0, int(spent.get("attempts", state.get("attempts", 0))))
    remaining = max(0, total - used)
    return RepairTree(
        finding_id=str(state.get("issue_id") or state.get("run_id") or "issue"),
        project_root=str(state.get("project") or ""),
        budget=Budget(
            max_attempts_per_finding=remaining,
            max_attempts_per_state=max(1, min(2, remaining)),
            max_total_attempts=remaining,
            max_depth=int(b.get("max_depth", 2)),
        ))
