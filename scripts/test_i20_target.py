"""I20 目标验证谓词测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.target_verification import (TargetObservation,  # noqa: E402
                                          TargetSpec, evaluate_target)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# Update：函数仍热，但 metric 降 + 调用=0
u = TargetSpec("f-update", "canvas_layout_churn", "frame_time",
               [50.0, 51.0, 49.0], min_delta=5.0,
               invariant="ForceUpdateCanvases == 0")
r = evaluate_target(u, TargetObservation(after=30.0, invariant_ok=True))
ok("metric 降 + 不变量 → verified", r.status == "verified", r)

# OpenShop：metric 降，但同步 Load 仍存在
o = TargetSpec("f-shop", "sync_resources_load_on_shop", "frame_time",
               [80.0, 82.0, 79.0], min_delta=5.0,
               invariant="per_shop_sync_loads == 0")
r = evaluate_target(o, TargetObservation(after=60.0, invariant_ok=False))
ok("只删字符串 → partial", r.status == "partial", r)

# ResolvePending：4→1，仍每帧扫描
p = TargetSpec("f-hint", "idle_hint_scan_per_frame", "frame_time",
               [20.0, 21.0, 19.0], min_delta=2.0,
               invariant="hasmatch_calls_per_frame == 0")
r = evaluate_target(p, TargetObservation(after=15.0, invariant_ok=False,
                                         pseudo=True))
ok("4→1 → pseudo", r.status == "pseudo", r)

# 指标未测
r = evaluate_target(u, TargetObservation(after=None))
ok("无 metric → unmeasured", r.status == "unmeasured", r)

# 站点剩余
r = evaluate_target(u, TargetObservation(after=30.0, invariant_ok=True,
                                         sites_remaining=2))
ok("站点剩余 → partial", r.status == "partial", r)

# 改善不过阈值
r = evaluate_target(u, TargetObservation(after=47.0, invariant_ok=True))
ok("改善不足 → needs_recheck", r.status == "needs_recheck", r)

print(f"\n==== I20 目标谓词测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
