"""采集分层策略（I12b）：由异常触发决定采多少，不看 action。

L0 常开标量 → L1 环缓冲 → L2 越界升级深档/分配栈 → L3 通用钩子。
"""
from __future__ import annotations

from dataclasses import dataclass

from upi.core.mode_caps import caps

TRIGGERS = {"frame_time", "gc_alloc", "draw_calls", "memory", "loading",
            "instantiate"}


@dataclass(frozen=True)
class CapturePlan:
    mode: str
    trigger: str
    pre_frames: int
    post_frames: int
    deep: bool
    alloc: bool
    hooks: tuple[str, ...]
    reason: str

    def model_dump(self) -> dict:
        return {"mode": self.mode, "trigger": self.trigger,
                "pre_frames": self.pre_frames, "post_frames": self.post_frames,
                "deep": self.deep, "alloc": self.alloc,
                "hooks": list(self.hooks), "reason": self.reason,
                "layers": ["L0", "L1", "L2", "L3"]}


def plan_for(mode: str, trigger: str, *, severity: float = 0.0) -> CapturePlan:
    if trigger not in TRIGGERS:
        raise ValueError(f"未知触发类型 {trigger!r}")
    c = caps(mode)
    # 常驻：事前多留（无法重放），事后短一些；回归可重放，前后均衡
    pre = 120 if mode == "resident" else 90
    post = 60 if mode == "resident" else 90
    hooks: list[str] = []
    if trigger == "loading":
        hooks += ["resources_load", "scene_load"]
    if trigger == "memory":
        hooks += ["asset_inventory", "texture_size"]
    if trigger == "instantiate":
        hooks += ["instantiate"]
    if trigger in ("frame_time", "draw_calls"):
        hooks += ["render_counters"]
    return CapturePlan(
        mode=mode, trigger=trigger,
        pre_frames=pre, post_frames=post,
        deep=True, alloc=True,
        hooks=tuple(dict.fromkeys(hooks)),
        reason=(f"{mode} 触发 {trigger}（severity={severity:.2f}），"
                f"沿环缓冲冻结 {pre} 帧并升级 {post} 帧"),
    )


def ring_capacity(mode: str) -> int:
    """L1 环缓冲保留帧数。"""
    return 300 if mode == "resident" else 600
