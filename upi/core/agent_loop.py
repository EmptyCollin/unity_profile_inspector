"""受控 Agent loop（I11d）：白名单工具 + 预算 + 确定性停止条件。

不是自由聊天：LLM 只能给 {tool,args} 决策，执行器按模式校验工具、扣预算、
把观察写回；越权/超预算/无进展即停。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from upi.core.mode_caps import assert_tool_allowed, caps


@dataclass
class LoopBudget:
    max_steps: int
    token_budget: int
    tokens_used: int = 0
    steps: int = 0

    def spend(self, tokens: int = 0) -> None:
        self.steps += 1
        self.tokens_used += max(0, int(tokens))

    @property
    def exhausted(self) -> bool:
        return self.steps >= self.max_steps or self.tokens_used >= self.token_budget


@dataclass
class LoopResult:
    stop_reason: str
    steps: int
    tokens: int
    trace: list[dict] = field(default_factory=list)


def run_loop(mode: str, decide: Callable[[dict], dict],
             act: Callable[[dict], dict], *,
             max_steps: Optional[int] = None,
             token_budget: Optional[int] = None,
             should_stop: Optional[Callable[[dict], bool]] = None,
             ) -> LoopResult:
    c = caps(mode)
    budget = LoopBudget(max_steps=max_steps or c.max_loop_steps,
                        token_budget=token_budget or c.token_budget)
    state: dict = {"mode": mode, "observations": []}
    trace: list[dict] = []
    stop_reason = "max_steps"
    while not budget.exhausted:
        decision = decide(state) or {}
        if decision.get("stop"):
            stop_reason = str(decision.get("reason") or "decided_stop")
            break
        tool = str(decision.get("tool") or "")
        try:
            assert_tool_allowed(mode, tool)
        except PermissionError as e:
            trace.append({"tool": tool, "denied": str(e)})
            stop_reason = "tool_denied"
            break
        try:
            obs = act(decision) or {}
        except Exception as e:  # noqa: BLE001
            obs = {"error": f"{type(e).__name__}: {e}"}
        budget.spend(int(decision.get("tokens") or 0))
        trace.append({"tool": tool, "args": decision.get("args") or {},
                      "observation": obs})
        state["observations"].append(obs)
        state["last"] = obs
        if should_stop is not None and should_stop(state):
            stop_reason = "condition_met"
            break
        if obs.get("error"):
            stop_reason = "tool_error"
            break
    return LoopResult(stop_reason=stop_reason, steps=budget.steps,
                      tokens=budget.tokens_used, trace=trace)
