"""I27 finding-first 契约。

核心原则：
- Finding 只记录“哪个窗口、哪个指标、越线多少、证据在哪”，不含 root_cause；
- window 是精确身份（cmd-10-open_shop），不是动作名；
- Candidate 属于 diagnose 内部，必须挂在 finding 上并继承/缩窄窗口；
- Verdict 分相对/绝对/环境三轴，避免拿一小时前的 baseline 判补丁。
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WindowRef(BaseModel):
    """精确动作窗口。window_id 是唯一身份，action 只用于展示/分组。"""

    model_config = ConfigDict(extra="forbid")

    window_id: str
    action: str = ""
    index: int = -1
    frame_start: int = 0
    frame_end: int = 0

    @model_validator(mode="after")
    def _fill_from_id(self):
        if self.window_id:
            parsed = _parse_window_id(self.window_id)
            if parsed is not None:
                index, action = parsed
                if self.index < 0:
                    self.index = index
                if not self.action:
                    self.action = action
        if not self.window_id and self.index >= 0 and self.action:
            object.__setattr__(self, "window_id", f"cmd-{self.index}-{self.action}")
        if self.window_id and not self.window_id.startswith("cmd-"):
            raise ValueError(f"非法 window_id：{self.window_id!r}（应为 cmd-<i>-<action>）")
        if self.frame_start and self.frame_end and self.frame_end < self.frame_start:
            raise ValueError("frame_end < frame_start")
        return self

    @classmethod
    def from_id(cls, window_id: str, *, frame_start: int = 0,
                frame_end: int = 0) -> "WindowRef":
        parsed = _parse_window_id(window_id)
        if parsed is None:
            raise ValueError(f"非法 window_id：{window_id!r}")
        index, action = parsed
        return cls(window_id=window_id, action=action, index=index,
                   frame_start=frame_start, frame_end=frame_end)


def _parse_window_id(window_id: str) -> tuple[int, str] | None:
    parts = str(window_id or "").split("-", 2)
    if len(parts) != 3 or parts[0] != "cmd" or not parts[2]:
        return None
    try:
        index = int(parts[1])
    except ValueError:
        return None
    if index < 0:
        return None
    return index, parts[2]


FindingState = Literal[
    "discovered", "diagnosing", "patched", "verified",
    "improved_partial", "needs_recheck", "semantic_risk",
    "environment_drift", "insufficient_evidence", "failed",
]


class Finding(BaseModel):
    """detect 产物：症状记录，不含根因。"""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    finding_id: str
    run_id: str = ""
    project_id: str = ""
    window: WindowRef
    metric: str
    unit: str = ""
    observed: dict = Field(default_factory=dict)   # median/max/n/series_ref
    reference: dict = Field(default_factory=dict)  # baseline/target/target_source
    magnitude: float = 0.0
    deviation_ratio: float = 0.0
    data_tier: str = "A2"
    evidence_refs: list[str] = Field(default_factory=list)
    state: FindingState = "discovered"
    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    @field_validator("evidence_refs")
    @classmethod
    def _needs_evidence(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Finding 至少需要一个 evidence_ref")
        return v


class RootCandidate(BaseModel):
    """diagnose 内部的根因假设；必须挂在 finding 上。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    finding_ids: list[str]
    window_ids: list[str] = Field(default_factory=list)
    scope: Literal["window", "global"] = "window"
    root_cause: str = ""
    mechanism: str = ""
    kind: str = ""
    anchors: list[dict] = Field(default_factory=list)
    score: float = 0.0
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    state: str = "candidate"

    @model_validator(mode="after")
    def _needs_owner_and_window(self):
        if not self.finding_ids:
            raise ValueError("Candidate 必须至少关联一个 finding_id")
        if not self.window_ids and self.scope != "global":
            raise ValueError("Candidate 没有 window_ids 时必须显式 scope=global")
        return self


class AcceptanceUnit(BaseModel):
    """验收分组：窗口相交或补丁冲突的 finding-candidate-patch 合为一组。"""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    finding_ids: list[str]
    candidate_ids: list[str] = Field(default_factory=list)
    patch_ids: list[str] = Field(default_factory=list)
    target_windows: list[WindowRef]
    control_windows: list[WindowRef] = Field(default_factory=list)
    merged_reason: str = ""

    @model_validator(mode="after")
    def _needs_target(self):
        if not self.target_windows:
            raise ValueError("AcceptanceUnit 必须有 target_windows")
        return self


VerdictStatus = Literal[
    "verified", "improved_partial_absolute_unmet",
    "improved_partial_absolute_unassessed", "not_significant", "regressed",
    "semantic_risk", "environment_drift", "insufficient_evidence", "needs_human",
]


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    relative: dict = Field(default_factory=dict)
    absolute: dict = Field(default_factory=dict)
    control: dict = Field(default_factory=dict)
    semantic: dict = Field(default_factory=dict)
    status: VerdictStatus = "insufficient_evidence"


def dedupe_hypotheses(hs: list) -> list[dict]:
    """同一 (root_cause, window_id, metric) 只留最高置信度。

    per-finding diagnose 会为每个 finding 重复产出同一批 candidate hypothesis；
    进 findings/units 前必须去重，否则 4 个 finding 会膨胀成 40 条。
    """
    best: dict[tuple, dict] = {}
    for h in hs or []:
        d = h.model_dump() if hasattr(h, "model_dump") else dict(h)
        key = (str(d.get("root_cause") or ""),
               str(d.get("window_id") or ""),
               str(d.get("finding_metric") or ""))
        try:
            conf = float(d.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if key not in best or conf > float(best[key].get("confidence") or 0.0):
            best[key] = d
    return sorted(best.values(),
                  key=lambda x: float(x.get("confidence") or 0.0),
                  reverse=True)
