"""I24：三次基线独立 counters，不做合并。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.targets import build_spec  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def counters(vals):
    return {"counters": {"CPU Main Thread Frame Time": {"series": vals}}}


runs = [counters([100, 100, 100]), counters([200, 200, 200]),
        counters([300, 300, 300])]
spec = build_spec({}, runs, metric="frame_time")
ok("三次基线取三个中位数", spec.baseline_values == [100.0, 200.0, 300.0],
   spec.baseline_values)

spec2 = build_spec({}, counters([1, 2, 3, 4, 5]), metric="frame_time")
ok("单 dict 保持 last3", spec2.baseline_values == [3.0, 4.0, 5.0],
   spec2.baseline_values)

print(f"\n==== I24 基线口径测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
