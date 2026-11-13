"""检查↔分析 双向证据请求（I11c）。

分析说“证据不够”时，给检查一个结构化请求；检查按当前模式能做什么就做什么，
做不到就明确 unavailable，不允许分析脑补。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from upi.core.mode_caps import caps

RequestKind = Literal["source", "callstack", "live_window", "replay_window",
                      "code_search", "ast_callgraph", "asset_inventory",
                      "reference_graph", "settings"]
_STATIC_KINDS = {"source", "code_search", "ast_callgraph", "asset_inventory",
                 "reference_graph", "settings"}


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: RequestKind
    reason: str
    metric: str = ""
    window: int = 0          # 帧数（live/replay）
    file: str = ""
    method: str = ""
    expected: str = ""
    factor: str = ""          # I18：缺哪个归因因子
    producer: str = ""        # I18：希望谁产（detect/verify/knowledge）


class EvidenceDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool
    kind: RequestKind
    text: str = ""
    payload: dict = Field(default_factory=dict)
    reason: str = ""
    evidence_class: str = "runtime"       # runtime | static
    snapshot_hash: str = ""
    can_support: list[str] = Field(default_factory=list)
    cannot_support: list[str] = Field(default_factory=list)
    limits: str = ""


def provider_kinds(mode: str) -> set[str]:
    c = caps(mode)
    kinds = set(_STATIC_KINDS)
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


def _static_delta(request: EvidenceRequest, index) -> EvidenceDelta:
    from upi.core.static_index import StaticIndex
    common = dict(evidence_class="static",
                  snapshot_hash=index.snapshot_hash,
                  can_support=StaticIndex.can_support(),
                  cannot_support=StaticIndex.cannot_support(),
                  limits="静态证据不证明运行时频率/耗时/是否执行")
    kind = request.kind
    if kind == "source":
        text = index.source_slice(request.file, request.method)
        return EvidenceDelta(available=bool(text), kind=kind, text=text,
                             payload={"file": request.file,
                                      "method": request.method}, **common,
                             reason="" if text else "源码片段读不出来")
    if kind == "code_search":
        rows = index.find_symbol(request.expected or request.method)
        return EvidenceDelta(available=bool(rows), kind=kind,
                             text="; ".join(f"{r['file']}::{r['method']}"
                                            for r in rows[:20]),
                             payload={"symbols": rows}, **common,
                             reason="" if rows else "未找到符号")
    if kind == "ast_callgraph":
        rows = index.callers(request.method or request.expected)
        return EvidenceDelta(available=bool(rows), kind=kind,
                             text="; ".join(f"{r['file']}::{r['method']}"
                                            for r in rows[:20]),
                             payload={"callers": rows}, **common,
                             reason="" if rows else "未找到调用点")
    if kind == "asset_inventory":
        rows = index.asset_inventory(kind=request.expected,
                                     min_size=max(0, request.window) * 1024)
        return EvidenceDelta(available=bool(rows), kind=kind,
                             text="; ".join(
                                 f"{r['file']}({r['kind']},{r['size']}B)"
                                 for r in rows[:20]),
                             payload={"assets": rows}, **common,
                             reason="" if rows else "资产清单为空")
    if kind == "reference_graph":
        rows = index.reference_graph(request.file)
        return EvidenceDelta(available=bool(rows), kind=kind,
                             text="; ".join(r["file"] for r in rows[:20]),
                             payload={"references": rows}, **common,
                             reason="" if rows else "未找到引用")
    if kind == "settings":
        summary = index.settings_summary()
        return EvidenceDelta(available=True, kind=kind,
                             text=json.dumps(summary, ensure_ascii=False)[:3000],
                             payload=summary, **common)
    return EvidenceDelta(available=False, kind=kind, **common,
                         reason=f"未知静态请求 {kind}")


def fulfill(request: EvidenceRequest, mode: str, project: Path | str, *,
            live_capture: Optional[Callable[[EvidenceRequest], Optional[dict]]] = None,
            replay_capture: Optional[Callable[[EvidenceRequest], Optional[dict]]] = None,
            static_index=None,
            ) -> EvidenceDelta:
    project = Path(project).resolve()
    if request.kind not in provider_kinds(mode):
        return EvidenceDelta(available=False, kind=request.kind,
                             reason=f"mode={mode} 不支持 {request.kind}")
    if request.kind in _STATIC_KINDS:
        try:
            if static_index is None:
                from upi.core.static_index import StaticIndex
                static_index = StaticIndex(project)
            return _static_delta(request, static_index)
        except Exception as e:  # noqa: BLE001
            return EvidenceDelta(available=False, kind=request.kind,
                                 reason=f"静态索引失败：{type(e).__name__}: {e}")
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
