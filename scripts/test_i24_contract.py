"""I24：修复契约与合算性。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_contract import (GuardBudget, RepairContract,  # noqa: E402
                                      evaluate)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


c = RepairContract(min_improve=0.05,
                   guards=[GuardBudget("memory", max_relative=0.10)])
r = evaluate(c, primary_before=100, primary_after=90,
             guards={"memory": (100 * 1024 * 1024, 100 * 1024 * 1024)})
ok("主指标改善 → verified", r.status == "verified", r)

r2 = evaluate(c, primary_before=100, primary_after=98)
ok("主指标不显著 → needs_recheck", r2.status == "needs_recheck", r2)

r3 = evaluate(c, primary_before=100, primary_after=90,
              guards={"memory": (100 * 1024 * 1024, 108 * 1024 * 1024)})
ok("内存换时间 → tradeoff", r3.status == "verified_with_tradeoff", r3)
ok("tradeoff net_ms>0", r3.details["net_ms"] > 0, r3.details)

r4 = evaluate(c, primary_before=100, primary_after=99,
              guards={"memory": (100 * 1024 * 1024, 140 * 1024 * 1024)})
ok("不划算 → needs_recheck", r4.status in ("needs_recheck", "regressed"), r4)

r5 = evaluate(c, primary_before=100, primary_after=90,
              guards={"memory": (100 * 1024 * 1024, 200 * 1024 * 1024)})
ok("guard 超预算 → regressed", r5.status == "regressed", r5)

print(f"\n==== I24 修复契约测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
