"""I8c：bootstrap / Monte Carlo 区间测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.intervals import bootstrap_ci, monte_carlo_interval  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


b = bootstrap_ci([10, 12, 11, 13, 9, 12, 11, 10])
ok("bootstrap 有分布", b["basis"] == "bootstrap" and b["n"] == 8)
ok("bootstrap 顺序", b["low"] <= b["point"] <= b["high"], b)

b1 = bootstrap_ci([42.0])
ok("单帧不冒充区间", b1["basis"] == "single_frame"
   and b1["low"] == b1["point"] == b1["high"], b1)

b0 = bootstrap_ci([])
ok("空样本", b0["n"] == 0 and b0["basis"] == "empty", b0)

m = monte_carlo_interval({"proximity": 1.0, "explains_share": 0.9,
                          "competitiveness": 0.7, "prior_hit": 0.5,
                          "counterfactual": 0.6, "mechanism": 0.9})
ok("Monte Carlo 有区间", m["basis"] == "monte_carlo"
   and m["low"] <= m["point"] <= m["high"], m)
ok("概率在 0..1", 0 <= m["low"] <= m["high"] <= 1, m)

m0 = monte_carlo_interval({}, n=500)
ok("空因子有先验", m0["point"] < 0.5 and m0["low"] <= m0["high"], m0)

print(f"\n==== 区间测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
