"""统一探针事件模型与事件日志（I1a）。

为什么先做 Python 侧事件总线，而不是直接重写 C# 探针：
- 现有两个注入件（Editor 常驻的 `UpiBreachProbe`、eval 注入的 `sampler_v3`）
  各自落盘、各自心跳。物理合并要动 Unity 回调/句柄/生命周期，必须真机验证；
- 先冻结“事件长什么样、怎么落盘、怎么消费”，后续 C# 合并只负责生产同一种
  事件，不会一边改 C# 一边改消费方；
- 兼容层把旧 `pb_breach.jsonl` / `data.json` 归一成 `ProbeEvent`，旧数据不废。

事件只有两类：
  breach —— 确定越界（单帧越过预算线），进修复循环；
  sample —— 不确定抽检（一段窗口的指标分布），只产候选，不直接进修复。
"""
from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from upi.core import events as E
from upi.core.models import A4Frame, BreachSample

EventKind = Literal["breach", "sample"]
DataTier = Literal["A", "A2", "A4"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:12]}"


def _median(vals: list[float]) -> float:
    return float(statistics.median(vals)) if vals else 0.0


def _p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(float(x) for x in vals)
    return float(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))])


class ProbeEvent(BaseModel):
    """一条探针事件；breach 与 sample 共用外壳，payload 按 kind 区分。"""

    event_id: str = Field(default_factory=lambda: _new_id("evt"))
    project_id: str = ""
    kind: EventKind
    ts: str = Field(default_factory=_now)
    session_id: str = ""
    source: str = "probe"

    data_tier: DataTier = "A2"
    protocol_fp: str = ""
    replay_seed: int = -1

    frame_idx: int = -1
    rel_frame: int = -1

    # breach 的标量字段
    metric: str = ""
    value: float = 0.0
    unit: str = ""
    threshold: float = 0.0
    deviation_ratio: float = 0.0
    ring_before: list[float] = Field(default_factory=list)
    ring_after: list[float] = Field(default_factory=list)
    a4_window: list[A4Frame] = Field(default_factory=list)

    # sample 的窗口字段
    series: list[float] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)

    raw_ref: str = ""
    meta: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _payload_matches_kind(self):
        if self.kind == "breach":
            if not self.metric:
                raise ValueError("breach 事件必须有 metric")
            if not self.ring_after:
                # 不强制深度（D17 由 BreachSample 契约把关），但空环通常意味着
                # 生产端只写了“响了”没写上下文；留痕可诊断，不在这里拒收。
                self.meta.setdefault("warning", "ring_after_empty")
        else:
            if not self.series and not self.metrics:
                raise ValueError("sample 事件必须有 series 或 metrics")
            if not self.metric and len(self.metrics) == 1:
                self.metric = next(iter(self.metrics))
        return self


def from_breach_sample(sample: BreachSample, *, session_id: str = "",
                       source: str = "breach_probe",
                       raw_ref: str = "") -> ProbeEvent:
    """旧 `pb_breach.jsonl` 的一条样本 → 统一 breach 事件。"""
    return ProbeEvent(
        kind="breach", session_id=session_id, source=source,
        data_tier=sample.data_tier, protocol_fp=sample.protocol_fingerprint,
        replay_seed=sample.replay_seed,
        frame_idx=sample.frame_idx, rel_frame=sample.rel,
        metric=sample.metric, value=float(sample.value),
        threshold=float(sample.threshold_abs),
        deviation_ratio=float(sample.deviation_ratio),
        ring_before=list(sample.ring_before), ring_after=list(sample.ring_after),
        a4_window=list(sample.a4_window),
        raw_ref=raw_ref,
        meta={"gc_ring_before": list(sample.gc_ring_before),
              "gc_ring_after": list(sample.gc_ring_after)},
    )


def events_from_a2(data: dict, *, session_id: str = "",
                   source: str = "a2_sampler",
                   raw_ref: str = "") -> list[ProbeEvent]:
    """旧 `data.json`（sampler_v3 的 series）→ 每个指标一条 sample 事件。

    兼容两种形态：
      - 生产 sampler：`{"frame": N, "series": {"Render/DrawCalls": [...]}}`
      - 摘要/旧束：`{"metrics": {"drawCalls": 47}}`
    """
    frame = int(data.get("frame") or data.get("frame_idx") or -1)
    protocol_fp = str(data.get("protocol_fp") or data.get("protocol_fingerprint") or "")
    out: list[ProbeEvent] = []

    series = data.get("series") or {}
    if isinstance(series, dict):
        for metric, vals in series.items():
            if not isinstance(vals, list) or not vals:
                continue
            nums = [float(x) for x in vals]
            out.append(ProbeEvent(
                kind="sample", session_id=session_id, source=source,
                data_tier="A2", protocol_fp=protocol_fp,
                frame_idx=frame, rel_frame=frame, metric=str(metric),
                series=nums, value=_median(nums),
                stats={"n": len(nums), "median": _median(nums),
                       "p95": _p95(nums), "min": min(nums), "max": max(nums)},
                raw_ref=raw_ref,
                meta={"why": data.get("why", ""), "skipped": data.get("skipped", [])},
            ))

    if not out:
        metrics = data.get("metrics") or {}
        if isinstance(metrics, dict) and metrics:
            nums = {str(k): float(v) for k, v in metrics.items() if isinstance(v, (int, float))}
            if nums:
                out.append(ProbeEvent(
                    kind="sample", session_id=session_id, source=source,
                    data_tier=str(data.get("data_tier") or "A2"),  # type: ignore[arg-type]
                    protocol_fp=protocol_fp, frame_idx=frame, rel_frame=frame,
                    metrics=nums, value=_median(list(nums.values())),
                    stats={"n": len(nums)}, raw_ref=raw_ref,
                ))
    return out


class EventLog:
    """`runs/_probe/probe_events.jsonl` 的追加写/读取（单一生产者，JSONL）。"""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path else (E.RUNS / "_probe" / "probe_events.jsonl")

    def append(self, event: ProbeEvent) -> None:
        if not event.project_id:
            try:
                from upi.config.project_profile import active_profile
                event.project_id = active_profile().profile_id
            except Exception:  # noqa: BLE001 - 无工程上下文时留空
                pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def read_all(self, *, skip_bad: bool = True, compat: bool = False) -> tuple[list[ProbeEvent], int]:
        """返回 (事件, 坏行数)。坏行不静默：计数给上层的“未评估”口径。

        compat=True 且统一日志尚不存在时，临时读旧 `pb_breach.jsonl` /
        `data.json` 归一（I1a 迁移桥）；统一日志一旦出现就只认它。
        """
        if not self.path.exists() or self.path.stat().st_size == 0:
            return self._read_compat() if compat else ([], 0)
        out: list[ProbeEvent] = []
        bad = 0
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return [], 0
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(ProbeEvent(**json.loads(line)))
            except Exception:  # noqa: BLE001 - 单行坏不拖垮整条流
                bad += 1
                if not skip_bad:
                    raise
        return out, bad

    def _read_compat(self) -> tuple[list[ProbeEvent], int]:
        """旧两源 → 统一事件；只在统一日志不存在时用。"""
        out: list[ProbeEvent] = []
        bad = 0
        probe_dir = E.RUNS / "_probe"

        breach = probe_dir / "pb_breach.jsonl"
        if breach.exists():
            try:
                lines = breach.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    s = BreachSample(**json.loads(line))
                    out.append(from_breach_sample(
                        s, session_id="legacy", source="legacy_breach",
                        raw_ref=str(breach)))
                except Exception:  # noqa: BLE001
                    bad += 1

        data = probe_dir / "data.json"
        if data.exists():
            try:
                d = json.loads(data.read_text(encoding="utf-8"))
                out.extend(events_from_a2(d, session_id="legacy",
                                          source="legacy_a2", raw_ref=str(data)))
            except Exception:  # noqa: BLE001
                bad += 1
        return out, bad

    def tail(self, n: int = 20, *, compat: bool = False) -> list[ProbeEvent]:
        evs, _ = self.read_all(compat=compat)
        return evs[-max(1, n):]

    def stats(self, *, compat: bool = False) -> dict:
        evs, bad = self.read_all(compat=compat)
        by_kind: dict[str, int] = {}
        for e in evs:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        return {"total": len(evs), "by_kind": by_kind, "bad_lines": bad,
                "last_ts": evs[-1].ts if evs else None}
