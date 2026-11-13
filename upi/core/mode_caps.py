"""模式能力表（I11a）：resident 与 regression 能做/不能做什么，单点定义。

硬边界：
- resident 不假设可复现：没有 replay 工具，不许写 verified；
- regression/考试有 replay：才允许 replay_once 与 3 基线 verified。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["resident", "regression"]


@dataclass(frozen=True)
class ModeCaps:
    mode: Mode
    evidence_providers: tuple[str, ...]
    verification_ladder: tuple[str, ...]
    max_evidence_rounds: int
    max_compile_retries: int
    max_loop_steps: int
    allow_replay: bool
    allow_verified: bool
    token_budget: int
    tools: frozenset[str] = field(default_factory=frozenset)

    def allows(self, tool: str) -> bool:
        return tool in self.tools


_TOOLS_COMMON = {"capture_window", "source_slice", "callstack", "history",
                 "plan_patch", "apply_patch", "compile", "smoke", "rollback"}
_TOOLS_REPLAY = {"replay_once", "baseline_run"}

CAPS: dict[str, ModeCaps] = {
    "resident": ModeCaps(
        mode="resident",
        evidence_providers=("live_window", "source", "callstack", "history"),
        verification_ladder=("compile", "smoke", "observe"),
        max_evidence_rounds=1,
        max_compile_retries=1,
        max_loop_steps=1,
        allow_replay=False,
        allow_verified=False,
        token_budget=4000,
        tools=frozenset(_TOOLS_COMMON),
    ),
    "regression": ModeCaps(
        mode="regression",
        evidence_providers=("live_window", "source", "callstack", "history",
                            "replay"),
        verification_ladder=("compile", "smoke", "replay_once", "baselines"),
        max_evidence_rounds=2,
        max_compile_retries=2,
        max_loop_steps=6,
        allow_replay=True,
        allow_verified=True,
        token_budget=12000,
        tools=frozenset(_TOOLS_COMMON | _TOOLS_REPLAY),
    ),
}


def caps(mode: str) -> ModeCaps:
    return CAPS.get(mode, CAPS["resident"])


def assert_tool_allowed(mode: str, tool: str) -> None:
    c = caps(mode)
    if not c.allows(tool):
        raise PermissionError(
            f"mode={mode} 不允许工具 {tool!r}（可用：{sorted(c.tools)}）")
