"""检查↔分析 双向证据请求（I11c）。

分析说“证据不够”时，给检查一个结构化请求；检查按当前模式能做什么就做什么，
做不到就明确 unavailable，不允许分析脑补。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from upi.core.mode_caps import caps

RequestKind = Literal["source", "callstack", "live_window", "replay_window"]


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: RequestKind
    reason: str
    metric: str = ""
    window: int = 0          # 帧数（live/replay）
    file: str = ""
    method: str = ""
    expected: str = ""


class EvidenceDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool
    kind: RequestKind
    text: str = ""
    payload: dict = Field(default_factory=dict)
    reason: str = ""


def provider_kinds(mode: str) -> set[str]:
    c = caps(mode)
    kinds = {"source", "callstack"}
    if "live_window" in c.evidence_providers:
        kinds.add("live_window")
    if c.allow_replay:
        kinds.add("replay_window")
    return kinds


def _source_slice(project: Path, file: str, method: str = "",
                  before: int = 20, after: int = 60) -> str:
    fp = (project / file).resolve()
    try:
        fp.relative_to(project.resolve())
    except ValueError:
        return ""
    if not fp.is_file():
        return ""
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    start = 0
    if method:
        m = method.split(".")[-1]
        for i, ln in enumerate(lines):
            if m + "(" in ln:
                start = i
                break
    lo, hi = max(0, start - before), min(len(lines), start + after)
    return "\n".join(lines[lo:hi])


def fulfill(request: EvidenceRequest, mode: str, project: Path | str, *,
            live_capture: Optional[Callable[[EvidenceRequest], Optional[dict]]] = None,
            replay_capture: Optional[Callable[[EvidenceRequest], Optional[dict]]] = None,
            ) -> EvidenceDelta:
    project = Path(project).resolve()
    if request.kind not in provider_kinds(mode):
        return EvidenceDelta(available=False, kind=request.kind,
                             reason=f"mode={mode} 不支持 {request.kind}")
    if request.kind == "source":
        text = _source_slice(project, request.file, request.method)
        if not text:
            return EvidenceDelta(available=False, kind="source",
                                 reason="源码片段读不出来")
        return EvidenceDelta(available=True, kind="source", text=text,
                             payload={"file": request.file,
                                      "method": request.method})
    cb = replay_capture if request.kind == "replay_window" else live_capture
    if cb is None:
        return EvidenceDelta(available=False, kind=request.kind,
                             reason="当前运行未注入对应采集器")
    try:
        payload = cb(request) or {}
    except Exception as e:  # noqa: BLE001
        return EvidenceDelta(available=False, kind=request.kind,
                             reason=f"{type(e).__name__}: {str(e)[:140]}")
    if not payload:
        return EvidenceDelta(available=False, kind=request.kind,
                             reason="采集器没有返回证据")
    return EvidenceDelta(available=True, kind=request.kind,
                         text=str(payload.get("text", ""))[:4000],
                         payload=payload)
