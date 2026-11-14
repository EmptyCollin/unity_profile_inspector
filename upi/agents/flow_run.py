"""I21：考试流程中枢（阶段/轮次/路由/落盘）。

不是业务判断：只决定下一步走哪条已定义阶段。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FlowState:
    run_id: str
    phase: str = "init"
    round: int = 0
    route: str = "proceed"
    active: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


class FlowRun:
    def __init__(self, run_dir: Path, run_id: str):
        self.dir = Path(run_dir)
        self.state = FlowState(run_id=run_id)

    def enter(self, phase: str, *, round_no: int | None = None,
              active: list[str] | None = None,
              verified: list[str] | None = None,
              route: str | None = None) -> FlowState:
        self.state.phase = phase
        if round_no is not None:
            self.state.round = round_no
        if active is not None:
            self.state.active = list(active)
        if verified is not None:
            self.state.verified = list(verified)
        if route is not None:
            self.state.route = route
        self.state.history.append({
            "phase": phase, "round": self.state.round,
            "route": self.state.route,
            "active": len(self.state.active),
            "verified": len(self.state.verified),
        })
        self.save()
        return self.state

    def decide(self, failed: set[str], *, round_no: int,
               max_rounds: int) -> str:
        """返回 next：retry / done / max_rounds。"""
        if not failed:
            self.enter("done", route="done")
            return "done"
        if round_no >= max_rounds:
            self.enter("max_rounds", route="max_rounds")
            return "max_rounds"
        self.enter("retry", route="retry", round_no=round_no)
        return "retry"

    def save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "flow.json").write_text(
                json.dumps({
                    "run_id": self.state.run_id,
                    "phase": self.state.phase,
                    "round": self.state.round,
                    "route": self.state.route,
                    "active": self.state.active,
                    "verified": self.state.verified,
                    "history": self.state.history,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
