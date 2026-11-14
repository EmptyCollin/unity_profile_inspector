"""I21：项目适配器边界。核心不假设项目类名、方法名或命令语义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Command:
    """项目自定义命令；UPI 不理解 name 的语义，只交给 adapter。"""
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    at: float | None = None


@dataclass(frozen=True)
class Replay:
    replay_id: str
    commands: list[Command]
    notes: str = ""


@dataclass(frozen=True)
class ProjectSpec:
    project_id: str
    root: Path
    metrics: tuple[str, ...] = ()
    adapter: str = ""


@dataclass
class CompileResult:
    ok: bool
    output: str = ""


@runtime_checkable
class ProjectAdapter(Protocol):
    def describe(self) -> ProjectSpec: ...

    def run_replay(self, replay: Replay) -> list[dict]: ...

    def capture(self, metrics: list[str]) -> dict: ...

    def compile(self) -> CompileResult: ...
