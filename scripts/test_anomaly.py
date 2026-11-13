"""I12c：异常驱动定位（只看序列，不看 action）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.anomaly import detect_series, merge_windows  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 正常序列：不报
normal = [(i, 10.0 + (i % 3) * 0.05) for i in range(120)]
ok("正常序列不报", detect_series("ft", normal) == [])

# 绝对门限
abs_pts = [(i, 10.0) for i in range(60)] + [(60, 99.0), (61, 98.0)]
w = detect_series("ft", abs_pts, threshold=50.0)
ok("绝对门限命中", any(x.kind == "absolute" and x.peak_value == 99.0
                   for x in w), w)

# 尖峰
spike = [(i, 10.0) for i in range(80)]
spike[50] = (50, 40.0)
w = detect_series("ft", spike, window=20, z=4.0)
ok("尖峰命中", any(x.kind == "spike" and x.start_frame == 50 for x in w), w)

# 阶跃
step = [(i, 10.0) for i in range(80)] + [(80 + i, 30.0) for i in range(80)]
w = detect_series("gc", step, window=20, z=4.0)
ok("阶跃命中", any(x.kind == "step" for x in w), w)

# 合并
a = detect_series("ft", [(i, 10.0) for i in range(40)] + [(40, 50.0)], window=15)
merged = merge_windows(a + a)
ok("窗口合并去重", len(merged) <= len(a), (len(a), len(merged)))

# 不含 action 语义：只接收 metric/points
import inspect  # noqa: E402
sig = inspect.signature(detect_series)
ok("检测接口无 action 参数", "action" not in sig.parameters, list(sig.parameters))

print(f"\n==== 异常检测测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
