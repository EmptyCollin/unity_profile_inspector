"""I8c 校准评测测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.calibration import (brier, ece, interval_coverage,  # noqa: E402
                                  reliability_table)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


rows = reliability_table([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1], bins=5)
ok("可靠性表分箱", len(rows) == 3 and all(r["n"] >= 1 for r in rows), rows)
ok("ECE 完全校准为 0", ece([0.0, 1.0], [0, 1], bins=5) == 0.0)
ok("Brier 范围", 0 <= brier([0.1, 0.9], [0, 1]) <= 1)
ok("区间覆盖", interval_coverage([(0, 2), (5, 6)], [1, 10])["coverage"] == 0.5,
   interval_coverage([(0, 2), (5, 6)], [1, 10]))
try:
    interval_coverage([(0, 1)], [1, 2])
    ok("长度不一致拒绝", False)
except ValueError:
    ok("长度不一致拒绝", True)

print(f"\n==== 校准评测测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
