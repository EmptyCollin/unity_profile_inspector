"""I26 P5：after 多轮中位数。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.targets import evaluate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def c(vals):
    return {"counters": {"CPU Main Thread Frame Time": {"series": vals}}}


spec, obs, verdict = evaluate(
    {}, [c([100, 100, 100]), c([200, 200, 200]), c([300, 300, 300])],
    [c([150, 150, 150]), c([160, 160, 160]), c([170, 170, 170])],
    metric="frame_time")
ok("before 三个中位数", spec.baseline_values == [100.0, 200.0, 300.0],
   spec.baseline_values)
ok("after 三轮中位数", obs.after == 160.0, obs.after)
ok("verdict 有输出", verdict.status in ("verified", "needs_recheck",
                                         "partial", "unmeasured"), verdict)

# dict after 保持兼容
_, obs2, _ = evaluate({}, c([100, 100, 100]), c([90, 90, 90]),
                      metric="frame_time")
ok("单 dict after", obs2.after == 90.0, obs2.after)

print(f"\n==== I26 after 多轮测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
