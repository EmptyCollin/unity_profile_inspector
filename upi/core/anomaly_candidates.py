"""异常窗口 → 候选（I12c）：把序列异常映射到窗口内函数/源码位置。

不读取 replay 命令类型；只消费事件时间线与证据包。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from upi.core.anomaly import AnomalyWindow, detect_series, merge_windows
from upi.core.candidates_v2 import CandidateV2
from upi.core.evidence_v2 import EvidenceBundleV2, SourceSpan
from upi.core.probe_events import ProbeEvent


def windows_from_events(events: Iterable[ProbeEvent]) -> list[dict]:
    by_metric: dict[str, list[tuple[int, float]]] = {}
    thresholds: dict[str, float] = {}
    for e in events:
        if e.kind == "breach" and e.metric:
            by_metric.setdefault(e.metric, []).append(
                (max(0, e.frame_idx), float(e.value)))
            if e.threshold:
                thresholds[e.metric] = max(thresholds.get(e.metric, 0.0),
                                           float(e.threshold))
        elif e.kind == "sample":
            if e.series and e.metric:
                metrics = {e.metric: e.series}
            elif e.metrics:
                metrics = e.metrics
            else:
                continue
            for metric, series in metrics.items():
                if not metric or not series:
                    continue
                start = max(0, e.frame_idx - len(series) + 1) if e.frame_idx >= 0 else 0
                pts = by_metric.setdefault(metric, [])
                pts.extend((start + i, float(v)) for i, v in enumerate(series))
    out: list[AnomalyWindow] = []
    for metric, pts in by_metric.items():
        pts = sorted(dict(pts).items())
        vals = [v for _, v in pts]
        thr = thresholds.get(metric)
        if thr is None and len(vals) >= 10:
            # 没有标定就不设绝对门限，只靠相对异常
            thr = None
        out.extend(detect_series(metric, pts, threshold=thr,
                                 window=min(30, max(5, len(pts) // 4))))
    return [w.model_dump() for w in merge_windows(out)]


def windows_from_bundle(bundle: EvidenceBundleV2, *,
                        max_windows: int = 20) -> list[dict]:
    """没有 breach 时，用证据本身的异常当触发：高分配帧/高热函数/高 drawCalls。"""
    out: list[dict] = []
    # 分配帧：按帧聚合字节数
    by_frame: dict[int, int] = {}
    for a in bundle.allocations:
        by_frame[int(a.frame_idx)] = by_frame.get(int(a.frame_idx), 0) + int(a.bytes)
    for i, (frame, total) in enumerate(sorted(by_frame.items(),
                                              key=lambda kv: kv[1], reverse=True)):
        if i >= 5:
            break
        out.append({"metric": "gc_alloc_per_frame", "start_frame": max(0, frame - 2),
                    "end_frame": frame + 2, "kind": "evidence_alloc",
                    "score": float(total) / 1024.0,
                    "reason": f"帧 {frame} 分配 {total}B"})
    # 函数：高热帧
    hot = sorted(bundle.functions, key=lambda f: f.total_ms, reverse=True)[:5]
    for f in hot:
        if f.total_ms < 5.0:
            continue
        out.append({"metric": "frame_time_ms",
                    "start_frame": max(0, int(f.frame_idx) - 2),
                    "end_frame": int(f.frame_idx) + 2,
                    "kind": "evidence_hot", "score": float(f.total_ms),
                    "reason": f"{f.symbol} 帧内 {f.total_ms}ms"})
    # 渲染：drawCalls 高的帧
    for r in sorted(bundle.render_objects, key=lambda x: x.draw_calls,
                    reverse=True)[:5]:
        if r.draw_calls < 100:
            continue
        out.append({"metric": "drawCalls",
                    "start_frame": max(0, int(r.frame_idx) - 2),
                    "end_frame": int(r.frame_idx) + 2,
                    "kind": "evidence_render", "score": float(r.draw_calls),
                    "reason": f"帧 {r.frame_idx} drawCalls={r.draw_calls}"})
    return out[:max_windows]


def candidates_from_anomalies(bundle: EvidenceBundleV2,
                              project_root: Path | str,
                              limit: int = 12) -> list[CandidateV2]:
    root = Path(project_root)
    out: list[CandidateV2] = []
    seen: set[tuple] = set()
    for w in bundle.anomalies:
        try:
            start, end = int(w["start_frame"]), int(w["end_frame"])
            metric = str(w["metric"])
        except (KeyError, TypeError, ValueError):
            continue
        frames = set(range(start, end + 1))
        funcs = [f for f in bundle.functions if f.frame_idx in frames]
        anchors: list[SourceSpan] = []
        for f in funcs[:8]:
            src = f.source
            if src is None:
                continue
            anchors.append(src)
        if not anchors:
            continue
        key = (metric, start, end)
        if key in seen:
            continue
        seen.add(key)
        score = float(w.get("score") or 0.0) + len(anchors)
        out.append(CandidateV2(
            id=f"cand-anomaly-{len(out)}",
            root_cause=f"anomaly:{metric}@{start}-{end}",
            mechanism=f"anomaly:{metric}", kind="anomaly",
            sources=anchors[:4], code_anchors=[],
            evidence_ids=[f"anomaly:{metric}:{start}:{end}"],
            score=score, score_interval_low=score, score_interval_high=score,
            interval_basis="anomaly_score", evidence_n=len(anchors),
            source_kind="runtime_anomaly"))
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]
