"""统一出口契约（I13d）：状态、落盘、模式守卫。

工作流只能从这些状态退出；每个出口必须写 exit_reason 与上下文。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from upi.core.mode_caps import caps

ExitCode = Literal[
    "blocked", "insufficient_evidence", "not_measured", "no_plan",
    "needs_recheck", "awaiting_approval", "rejected", "applied_unverified",
    "rolled_back", "failed", "skipped", "unsafe_abort", "verified",
    "observed", "regressed", "unverified", "protocol_mismatch",
    "not_reproduced", "budget_exceeded", "stagnant", "tool_error",
    "usability_failed",
]

EXIT_CODES: frozenset[str] = frozenset({
    "blocked", "insufficient_evidence", "not_measured", "no_plan",
    "needs_recheck", "awaiting_approval", "rejected", "applied_unverified",
    "rolled_back", "failed", "skipped", "unsafe_abort", "verified",
    "observed", "regressed", "unverified", "protocol_mismatch",
    "not_reproduced", "budget_exceeded", "stagnant", "tool_error",
    "usability_failed",
})

# 图里作为终态、不再继续走后续节点的 route
TERMINAL_ROUTES = frozenset({
    "needs_human", "blocked", "insufficient_evidence", "not_measured",
    "no_plan", "not_reproduced", "budget_exceeded", "unsafe_abort",
    "usability_failed",
})


def is_terminal(route: str) -> bool:
    return route in TERMINAL_ROUTES


def assert_exit_allowed(mode: str, code: str) -> None:
    """G21：常驻不许写 verified。"""
    if code not in EXIT_CODES:
        raise ValueError(f"未知出口状态 {code!r}")
    if code == "verified" and not caps(mode).allow_verified:
        raise PermissionError(f"mode={mode} 不允许退出状态 verified")


def record_exit(run_dir: Path | str, *, stage: str, code: str,
                reason: str, mode: str = "regression",
                details: dict | None = None) -> dict:
    """把一次出口写入 run_dir/exit.json（同一 run 只保留最后一次）。"""
    assert_exit_allowed(mode, code)
    payload = {"stage": stage, "code": code, "reason": reason,
               "mode": mode, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "details": details or {}}
    p = Path(run_dir) / "exit.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return payload
