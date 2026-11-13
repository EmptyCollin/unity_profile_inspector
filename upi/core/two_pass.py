"""两遍 replay（I13a）：r 找窗口，r′ 完整重放、只在窗口内开深档。

本模块只做“窗口选择 / 余量 / 编排”，不直接调 Unity；
真实执行由 capability_run 注入 pass1/pass2。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class DeepWindow:
    metric: str
    start_frame: int
    end_frame: int
    score: float = 0.0
    kind: str = ""
    coverage_gap: tuple[str, ...] = ()

    def with_margin(self, margin: int) -> "DeepWindow":
        return DeepWindow(metric=self.metric,
                          start_frame=max(0, self.start_frame - margin),
                          end_frame=self.end_frame + margin,
                          score=self.score, kind=self.kind,
                          coverage_gap=self.coverage_gap)

    def model_dump(self) -> dict:
        return {"metric": self.metric, "start_frame": self.start_frame,
                "end_frame": self.end_frame, "score": self.score,
                "kind": self.kind, "coverage_gap": list(self.coverage_gap),
                "frame_range": [self.start_frame, self.end_frame]}


def select_windows(windows: list[dict], *, max_n: int = 3,
                   min_gap: int = 30) -> list[DeepWindow]:
    """按分数选最多 max_n 个窗口；同 metric 邻近窗口合并。"""
    parsed: list[DeepWindow] = []
    for w in windows:
        try:
            parsed.append(DeepWindow(
                metric=str(w["metric"]),
                start_frame=int(w["start_frame"]),
                end_frame=int(w["end_frame"]),
                score=float(w.get("score") or 0.0),
                kind=str(w.get("kind") or ""),
                coverage_gap=tuple(str(x) for x in (w.get("coverage_gap") or []))))
        except (KeyError, TypeError, ValueError):
            continue
    # 覆盖缺口优先：分数 + 缺口加权
    parsed.sort(key=lambda w: (w.score + 5.0 * len(w.coverage_gap)), reverse=True)
    picked: list[DeepWindow] = []
    for w in parsed:
        if len(picked) >= max_n:
            break
        dup = next((p for p in picked
                    if p.metric == w.metric
                    and abs(p.start_frame - w.start_frame) < min_gap), None)
        if dup is not None:
            # 同 metric 邻近：保留分数更高者，扩展范围
            dup.start_frame = min(dup.start_frame, w.start_frame)
            dup.end_frame = max(dup.end_frame, w.end_frame)
            dup.coverage_gap = tuple(dict.fromkeys(
                list(dup.coverage_gap) + list(w.coverage_gap)))
            continue
        picked.append(w)
    picked.sort(key=lambda w: w.start_frame)
    return picked


def plan_deep_windows(windows: list[dict], *, max_n: int = 3,
                      margin: int = 12, min_gap: int = 30) -> list[DeepWindow]:
    return [w.with_margin(margin)
            for w in select_windows(windows, max_n=max_n, min_gap=min_gap)]


def run_two_pass(pass1: Callable[[], dict],
                 pass2: Callable[[list[DeepWindow]], dict], *,
                 max_n: int = 3, margin: int = 12) -> dict:
    """r → 选窗 → r′。没有窗口就只保留 r，不硬采深档。"""
    first = pass1() or {}
    windows = plan_deep_windows(first.get("windows") or [],
                                max_n=max_n, margin=margin)
    if not windows:
        return {"status": "no_window", "pass1": first, "windows": [],
                "pass2": None, "reason": "r 未产生异常窗口"}
    try:
        second = pass2(windows) or {}
    except Exception as e:  # noqa: BLE001
        return {"status": "unavailable", "pass1": first,
                "windows": [w.model_dump() for w in windows],
                "pass2": None,
                "reason": f"r′ 执行失败：{type(e).__name__}: {e}"}
    return {"status": "done", "pass1": first,
            "windows": [w.model_dump() for w in windows],
            "pass2": second, "reason": ""}
