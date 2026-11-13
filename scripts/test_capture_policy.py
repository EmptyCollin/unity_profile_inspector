"""I12b：采集分层策略（触发式、无 action、有预算）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.capture_policy import plan_for, ring_capacity  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


for mode in ("resident", "regression"):
    p = plan_for(mode, "frame_time")
    ok(f"{mode} 有 pre/post", p.pre_frames > 0 and p.post_frames > 0, p)
    ok(f"{mode} 升级深档+分配", p.deep and p.alloc)
    ok(f"{mode} 环缓冲 > 0", ring_capacity(mode) > 0)

ok("loading 触发加载钩子",
   "resources_load" in plan_for("resident", "loading").hooks)
ok("memory 触发资产钩子",
   "asset_inventory" in plan_for("resident", "memory").hooks)
try:
    plan_for("resident", "open_shop")
    ok("未知触发被拒", False)
except ValueError:
    ok("未知触发被拒", True)

p = plan_for("regression", "gc_alloc")
d = p.model_dump()
ok("计划不带 action 字段", "action" not in d and "command" not in d, d.keys())
ok("resident 事前更长", plan_for("resident", "frame_time").pre_frames
   >= plan_for("regression", "frame_time").pre_frames)

print(f"\n==== 采集策略测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
