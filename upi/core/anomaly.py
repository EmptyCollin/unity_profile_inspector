"""异常驱动定位（I12c）：只看时间序列，不看 action/命令类型。

输入是 (frame, value) 序列；输出是异常窗口。支持：
- 绝对门限；
- 滚动中位数 + MAD（稳健）；
- 均值阶跃（变点）；
- 突增（相对前一窗）。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class AnomalyWindow:
    metric: str
    start_frame: int
    end_frame: int
    kind: str                 # absolute | spike | step
    peak_value: float
    baseline: float
    score: float
    reason: str = ""
    frames: list[int] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {"metric": self.metric, "start_frame": self.start_frame,
                "end_frame": self.end_frame, "kind": self.kind,
                "peak_value": self.peak_value, "baseline": self.baseline,
                "score": round(self.score, 3), "reason": self.reason,
                "frame_range": [self.start_frame, self.end_frame]}


def _median(vals: list[float]) -> float:
    return statistics.median(vals) if vals else 0.0


def _mad(vals: list[float], med: float) -> float:
    if not vals:
        return 0.0
    return statistics.median(abs(v - med) for v in vals)


def detect_series(metric: str, points: list[tuple[int, float]], *,
                  threshold: float | None = None,
                  window: int = 30, z: float = 4.0,
                  min_abs: float = 0.0) -> list[AnomalyWindow]:
    """points 需按 frame 升序。没有明显异常就返回空。"""
    if len(points) < 3:
        return []
    frames = [f for f, _ in points]
    vals = [v for _, v in points]
    out: list[AnomalyWindow] = []

    # 1) 绝对门限
    if threshold is not None:
        bad = [(f, v) for f, v in points if v > threshold]
        if bad:
            out.append(AnomalyWindow(
                metric=metric, start_frame=bad[0][0], end_frame=bad[-1][0],
                kind="absolute", peak_value=max(v for _, v in bad),
                baseline=threshold, score=len(bad) / max(1, len(points)),
                reason=f"{len(bad)} 帧超过绝对门限 {threshold}", frames=[f for f, _ in bad]))

    # 2) 滚动基线 spike
    for i in range(window, len(vals)):
        base = vals[max(0, i - window):i]
        med = _median(base)
        mad = _mad(base, med)
        scale = max(mad * 1.4826, abs(med) * 0.02, 1e-9)
        v = vals[i]
        if v > med + z * scale and (v - med) >= min_abs:
            # 合并相邻
            if out and out[-1].kind == "spike" and frames[i] - out[-1].end_frame <= 2:
                w = out[-1]
                w.end_frame = frames[i]
                w.peak_value = max(w.peak_value, v)
                w.score = max(w.score, (v - med) / scale)
                w.frames.append(frames[i])
            else:
                out.append(AnomalyWindow(
                    metric=metric, start_frame=frames[i], end_frame=frames[i],
                    kind="spike", peak_value=v, baseline=med,
                    score=(v - med) / scale,
                    reason=f"相对前 {window} 帧中位数 {med:.3f} 突增",
                    frames=[frames[i]]))

    # 3) 阶跃：后半窗均值显著高于前半窗
    if len(vals) >= window * 2:
        for i in range(window, len(vals) - window + 1):
            a = vals[i - window:i]
            b = vals[i:i + window]
            ma, mb = statistics.mean(a), statistics.mean(b)
            sd = statistics.pstdev(a) or 1e-9
            if mb > ma + z * sd and (mb - ma) >= min_abs:
                out.append(AnomalyWindow(
                    metric=metric, start_frame=frames[i],
                    end_frame=frames[min(i + window - 1, len(frames) - 1)],
                    kind="step", peak_value=max(b), baseline=ma,
                    score=(mb - ma) / sd,
                    reason=f"均值阶跃 {ma:.3f}→{mb:.3f}"))
                break           # 一个序列只报一个阶跃
    return out


def merge_windows(windows: list[AnomalyWindow]) -> list[AnomalyWindow]:
    """合并同 metric 且帧范围相邻/重叠的窗口，去重。"""
    out: list[AnomalyWindow] = []
    for w in sorted(windows, key=lambda x: (x.metric, x.start_frame)):
        if (out and out[-1].metric == w.metric
                and w.start_frame <= out[-1].end_frame + 2):
            p = out[-1]
            p.end_frame = max(p.end_frame, w.end_frame)
            p.peak_value = max(p.peak_value, w.peak_value)
            p.score = max(p.score, w.score)
            p.kind = p.kind if p.kind == w.kind else "mixed"
            p.frames = sorted(set(p.frames + w.frames))
        else:
            out.append(w)
    return out
