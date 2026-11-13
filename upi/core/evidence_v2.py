"""I8a 证据 v2：帧指标之外的调用栈/函数/对象/分配证据。

设计边界：
- 这里只定义契约、落盘与适配，不伪造 Unity 没给的数据；
- 拿不到调用栈/对象级数据时必须写 BlindSpot，不能留空当成“没有热点”；
- 原始证据落 runs/<id>/evidence/，后续候选生成只读证据，不读 case/flag。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from upi.core import events as E
from upi.core.models import DataTier

EvidenceTier = Literal["A2", "A4", "B", "C", "unavailable"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class SourceSpan(BaseModel):
    """源码位置；file 为工程内相对路径，行号 1-based。"""

    file: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    symbol: str = ""

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line 不能小于 start_line")
        return self


class CallStackFrame(BaseModel):
    symbol: str = ""
    source: Optional[SourceSpan] = None
    native: bool = False
    address: str = ""


class CallStackSample(BaseModel):
    frame_idx: int = Field(ge=0)
    thread: int = 0
    weight: float = Field(default=1.0, gt=0)
    frames: list[CallStackFrame] = Field(min_length=1)
    metric_context: dict[str, float] = Field(default_factory=dict)
    data_tier: EvidenceTier = "A2"
    raw_ref: str = ""


class FunctionStat(BaseModel):
    frame_idx: int = Field(ge=0)
    symbol: str = Field(min_length=1)
    calls: int = Field(default=0, ge=0)
    total_ms: float = Field(default=0.0, ge=0)
    self_ms: float = Field(default=0.0, ge=0)
    source: Optional[SourceSpan] = None
    data_tier: EvidenceTier = "A2"


class RenderObjectStat(BaseModel):
    frame_idx: int = Field(ge=0)
    renderer_path: str = Field(min_length=1)
    material_ids: list[str] = Field(default_factory=list)
    shader: str = ""
    draw_calls: int = Field(default=0, ge=0)
    batches: int = Field(default=0, ge=0)
    triangles: int = Field(default=0, ge=0)
    data_tier: EvidenceTier = "A2"


class AllocationSite(BaseModel):
    frame_idx: int = Field(ge=0)
    bytes: int = Field(default=0, ge=0)
    count: int = Field(default=0, ge=0)
    stack: list[CallStackFrame] = Field(default_factory=list)
    source: Optional[SourceSpan] = None
    data_tier: EvidenceTier = "A2"


class BlindSpot(BaseModel):
    code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    unlock: str = ""


_EDITOR_MARKERS = (
    "UnityEditor.", "EditorLoop", "GameView.", "HierarchyWindow",
    "DockArea.", "UIElements.", "EditorApplication.",
)


def is_editor_symbol(symbol: str) -> bool:
    return any(m in (symbol or "") for m in _EDITOR_MARKERS)


def clean_game_evidence(bundle: EvidenceBundleV2) -> EvidenceBundleV2:
    """剔除 Editor 发起的调用栈/分配；保留原始证据包不变。

    真实游戏也可能被 Editor 驱动，所以不能悄悄删：丢失数量写 blindspot。
    """
    kept_cs = [cs for cs in bundle.callstacks
               if not any(is_editor_symbol(f.symbol) for f in cs.frames)]
    kept_alloc = [a for a in bundle.allocations
                  if not any(is_editor_symbol(f.symbol) for f in a.stack)]
    dropped_cs = len(bundle.callstacks) - len(kept_cs)
    dropped_alloc = len(bundle.allocations) - len(kept_alloc)
    b = bundle.model_copy(deep=True)
    b.callstacks = kept_cs
    b.allocations = kept_alloc
    b.meta = dict(b.meta)
    b.meta["editor_filter"] = {"dropped_callstacks": dropped_cs,
                               "dropped_allocations": dropped_alloc}
    if (dropped_cs or dropped_alloc) and not any(
            x.code == "editor_contaminated" for x in b.blindspots):
        b.blindspots.append(BlindSpot(
            code="editor_contaminated",
            reason=f"剔除了 {dropped_cs} 条 Editor 调用栈、{dropped_alloc} 个 Editor 分配点",
            unlock="在 Play Mode 中由游戏输入驱动，而不是 Editor UI 驱动",
        ))
    return b


class EvidenceBundleV2(BaseModel):
    """一次采集的完整证据包；空包必须带 blindspot，不允许静默空。"""

    run_id: str
    project_id: str = ""
    scene: str = ""
    protocol_fingerprint: str = ""
    captured_at: str = Field(default_factory=_now)
    frames: list[int] = Field(default_factory=list)
    callstacks: list[CallStackSample] = Field(default_factory=list)
    functions: list[FunctionStat] = Field(default_factory=list)
    render_objects: list[RenderObjectStat] = Field(default_factory=list)
    allocations: list[AllocationSite] = Field(default_factory=list)
    anomalies: list[dict] = Field(default_factory=list)
    counters: dict = Field(default_factory=dict)
    coverage: dict = Field(default_factory=dict)
    blindspots: list[BlindSpot] = Field(default_factory=list)
    raw_refs: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _not_silently_empty(self):
        has_evidence = bool(self.frames or self.callstacks or self.functions
                            or self.render_objects or self.allocations)
        if not has_evidence and not self.blindspots:
            raise ValueError("EvidenceBundleV2 不能为空且无 blindspot（I1）")
        return self

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    def write(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        E.atomic_write_text(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2))
        return path


class EvidenceStore:
    """`runs/<run_id>/evidence/evidence_v2.json` 的读写。"""

    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root) if root else E.RUNS

    def save(self, bundle: EvidenceBundleV2, *, name: str = "evidence_v2.json") -> Path:
        out = self.root / bundle.run_id / "evidence" / name
        return bundle.write(out)

    def load(self, path: Path | str) -> EvidenceBundleV2:
        return EvidenceBundleV2(**json.loads(Path(path).read_text(encoding="utf-8")))

    def latest(self) -> Optional[Path]:
        files = sorted(self.root.glob("*/evidence/evidence_v2.json"),
                       key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None


def from_probe_events(events: list, *, run_id: str, project_id: str = "",
                      scene: str = "", protocol_fingerprint: str = "",
                      raw_refs: Optional[list[str]] = None) -> EvidenceBundleV2:
    """把统一探针事件转成证据包。

    探针事件本身只有帧指标；调用栈/对象统计由未来 deep capture 适配器填入。
    这里如实标 blindspot，不把“没采到”说成“没有”。
    """
    frames: set[int] = set()
    callstacks: list[CallStackSample] = []
    functions: list[FunctionStat] = []
    render_objects: list[RenderObjectStat] = []
    allocations: list[AllocationSite] = []

    for ev in events:
        fi = int(getattr(ev, "frame_idx", -1))
        if fi >= 0:
            frames.add(fi)
        meta = getattr(ev, "meta", {}) or {}
        # 预留：外部 deep capture 可以挂在这些字段上；不伪造来源。
        for item in meta.get("callstacks", []) or []:
            callstacks.append(CallStackSample(**item))
        for item in meta.get("functions", []) or []:
            functions.append(FunctionStat(**item))
        for item in meta.get("render_objects", []) or []:
            render_objects.append(RenderObjectStat(**item))
        for item in meta.get("allocations", []) or []:
            allocations.append(AllocationSite(**item))

    blindspots: list[BlindSpot] = []
    if not callstacks:
        blindspots.append(BlindSpot(
            code="callstack_unavailable",
            reason="本证据包没有调用栈；探针只提供帧级计数器",
            unlock="接入 Unity Profiler binary log / Deep Profile 适配器",
        ))
    if not functions:
        blindspots.append(BlindSpot(
            code="function_stats_unavailable",
            reason="本证据包没有函数调用次数/耗时",
            unlock="接入 ProfilerRecorder marker 或 Profiler 时间线",
        ))
    if not render_objects:
        blindspots.append(BlindSpot(
            code="render_object_unavailable",
            reason="本证据包没有对象级 draw call 归因",
            unlock="接入 FrameDebugger / RenderDoc 对象事件",
        ))

    return EvidenceBundleV2(
        run_id=run_id, project_id=project_id, scene=scene,
        protocol_fingerprint=protocol_fingerprint,
        frames=sorted(frames), callstacks=callstacks, functions=functions,
        render_objects=render_objects, allocations=allocations,
        blindspots=blindspots, raw_refs=list(raw_refs or []),
        meta={"source": "probe_events"},
    )
