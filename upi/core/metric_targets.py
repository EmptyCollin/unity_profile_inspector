"""I27：验收 absolute target 注册表。

来源：
- frame_time：`knowledge/frame_budget.json` target60 = 16.667ms；
  counters 的 frame_time 单位是 ns（finding_detect.py 注册）。
- gc_alloc：`finding_detect.ABSOLUTE_MIN` 的单窗分配门槛 64KB。
没有注册的指标保持 unassessed，禁止临时估一个数（I1）。
"""
from __future__ import annotations

# frame_time: 16.667 ms -> ns；gc_alloc: 64 KiB
TARGETS: dict[str, float] = {
    "frame_time": 16_667_000.0,
    "cpu_frame_time": 16_667_000.0,
    "gc_alloc": 64.0 * 1024.0,
    "gc_alloc_bytes": 64.0 * 1024.0,
}


def target_for(metric: str) -> float | None:
    m = str(metric or "").lower()
    return TARGETS.get(m)
