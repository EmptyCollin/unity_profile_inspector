"""replay 命令协议：项目指令序列，不是工程内配置表。

命令由 UPI 工具执行；Agent 不读 oracle/期望结果。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

CommandType = Literal[
    "start_game", "load_scene", "spawn", "rotate", "move", "zoom",
    "swap", "wait", "stop",
]


class ReplayCommand(BaseModel):
    type: CommandType
    seconds: Optional[float] = None
    count: Optional[int] = None
    degrees: Optional[float] = None
    distance: Optional[float] = None
    zoom: Optional[float] = None
    scene: Optional[str] = None
    from_cell: Optional[tuple[int, int]] = None
    to_cell: Optional[tuple[int, int]] = None

    @model_validator(mode="after")
    def _required(self):
        need = {
            "wait": ("seconds", self.seconds),
            "spawn": ("count", self.count),
            "rotate": ("degrees", self.degrees),
            "move": ("distance", self.distance),
            "zoom": ("zoom", self.zoom),
            "load_scene": ("scene", self.scene),
            "swap": ("from_cell", self.from_cell),
        }
        if self.type in need:
            name, val = need[self.type]
            if val is None:
                raise ValueError(f"{self.type} 缺少 {name}")
        if self.type == "swap" and self.to_cell is None:
            raise ValueError("swap 缺少 to_cell")
        if self.seconds is not None and self.seconds < 0:
            raise ValueError("seconds 不能为负")
        if self.count is not None and self.count <= 0:
            raise ValueError("count 必须为正")
        return self


class ReplaySpec(BaseModel):
    replay_id: str
    base_hash: str = ""
    commands: list[ReplayCommand] = Field(min_length=1)
    notes: str = ""


def load_replay(path: Path | str) -> ReplaySpec:
    return ReplaySpec(**json.loads(Path(path).read_text(encoding="utf-8")))
