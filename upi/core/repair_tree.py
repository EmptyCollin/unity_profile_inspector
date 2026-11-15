"""I25：修复 checkpoint 状态树与预算。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

KEEP = {"improved_partial", "verified"}
FAIL = {"no_effect", "regressed"}
INFRA = {"infra_failure"}


@dataclass
class Budget:
    max_attempts_per_finding: int = 6
    max_attempts_per_state: int = 2
    max_depth: int = 2
    max_branches: int = 2
    max_total_attempts: int = 12
    max_captures: int = 24
    max_wallclock_s: int = 3600
    max_tokens: int = 200000

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def load_budget(path: str | Path | None = None) -> Budget:
    """从 config/repair_budget.yaml 读预算；缺省用默认值。"""
    import yaml
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[2]
    p = _Path(path) if path else root / "config" / "repair_budget.yaml"
    data = {}
    if p.is_file():
        try:
            data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get(
                "budget") or {}
        except Exception:  # noqa: BLE001
            data = {}
    defaults = Budget()
    vals = {}
    for k, default in defaults.as_dict().items():
        try:
            v = int(data.get(k, default))
        except (TypeError, ValueError):
            v = default
        vals[k] = max(0, v)
    return Budget(**vals)


@dataclass
class Attempt:
    state_id: str
    attempt_id: str
    status: str
    patch_id: str = ""
    delta: Optional[float] = None
    reason: str = ""


@dataclass
class State:
    state_id: str
    parent_id: str = ""
    depth: int = 0
    status: str = "active"
    metric_before: Optional[float] = None
    accepted_patch: str = ""
    attempts_at_state: int = 0
    children: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class RepairTree:
    """DFS 修复树：保留改善，失败回退父 checkpoint。"""

    def __init__(self, *, finding_id: str, budget: Budget | None = None,
                 metric_before: float | None = None):
        self.finding_id = finding_id
        self.budget = budget or Budget()
        self.states: dict[str, State] = {}
        self.attempts: list[Attempt] = []
        self.current_id = "root"
        self.root_id = "root"
        self.finding_failures = 0
        self.total_attempts = 0
        self.finished = False
        self.reason = ""
        self.states["root"] = State("root", metric_before=metric_before)

    @property
    def current(self) -> State:
        return self.states[self.current_id]

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{len(self.states)}"

    def record(self, status: str, *, patch_id: str = "",
               delta: float | None = None, reason: str = "") -> str:
        """记录一次 attempt，返回 keep / retry / backtrack / finding_failed /
        budget_exhausted / infra_retry / depth_exhausted。"""
        if self.finished:
            return "finding_failed"
        if status not in KEEP and status not in FAIL and status not in INFRA:
            status = "no_effect"
        self.total_attempts += 1
        self.attempts.append(Attempt(
            state_id=self.current_id, attempt_id=f"a{len(self.attempts)}",
            status=status, patch_id=patch_id, delta=delta, reason=reason))

        if status in INFRA:
            # 基础设施失败不扣优化配额，仍允许重试
            return "infra_retry"

        if status in KEEP:
            if self.current.depth >= self.budget.max_depth:
                self.finished = True
                self.reason = "max_depth"
                self.current.status = "best"
                return "depth_exhausted"
            child_id = self._new_id("s")
            child = State(child_id, parent_id=self.current_id,
                          depth=self.current.depth + 1,
                          metric_before=self.current.metric_before,
                          accepted_patch=patch_id)
            self.states[child_id] = child
            self.current.status = "best"
            self.current.children.append(child_id)
            self.current_id = child_id
            self.finding_failures = 0
            return "keep"

        if status in FAIL:
            self.finding_failures += 1
            self.current.attempts_at_state += 1
            if self.total_attempts >= self.budget.max_total_attempts:
                self.finished = True
                self.reason = "max_total_attempts"
                return "budget_exhausted"
            if self.finding_failures >= self.budget.max_attempts_per_finding:
                self.finished = True
                self.reason = "max_attempts_per_finding"
                self.states[self.root_id].status = "failed"
                return "finding_failed"
            if (self.current.attempts_at_state
                    >= self.budget.max_attempts_per_state):
                if self.current.parent_id:
                    self.current.status = "abandoned"
                    self.current_id = self.current.parent_id
                    return "backtrack"
                # 根状态没有父可退：继续消耗 finding 失败配额
                return "retry"
            return "retry"

        return "retry"

    def rollback_to(self, state_id: str) -> bool:
        if state_id not in self.states:
            return False
        self.current_id = state_id
        self.finished = False
        self.reason = ""
        return True

    def root_failed(self) -> bool:
        return self.states[self.root_id].status == "failed"

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "budget": self.budget.as_dict(),
            "current": self.current_id,
            "root_failed": self.root_failed(),
            "finished": self.finished,
            "reason": self.reason,
            "total_attempts": self.total_attempts,
            "states": [s.as_dict() for s in self.states.values()],
            "attempts": [a.__dict__ for a in self.attempts],
        }


def save_tree(tree: RepairTree, path: str | Path) -> Path:
    import json
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tree.to_dict(), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p
