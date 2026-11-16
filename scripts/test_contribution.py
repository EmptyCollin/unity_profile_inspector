"""多因判据测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.contribution import (Contribution, group_decision,  # noqa: E402
                                   individual_ok)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("绝对值达标", individual_ok(
    Contribution("a", abs_cost=0.5, share=0.1), min_abs=0.2,
    min_share=0.3, major_abs=1.0))
ok("绝对值不足拒绝", not individual_ok(
    Contribution("a", abs_cost=0.1, share=0.9), min_abs=0.2,
    min_share=0.3, major_abs=1.0))
ok("大绝对值允许低占比", individual_ok(
    Contribution("a", abs_cost=1.5, share=0.05), min_abs=0.2,
    min_share=0.3, major_abs=1.0))
items = [Contribution("a", 0.5, 0.12), Contribution("b", 0.4, 0.09),
         Contribution("c", 0.3, 0.07)]
d = group_decision(items, min_abs=0.2, min_share=0.3, major_abs=1.0,
                   group_share=0.25)
ok("多因组合通过", d["eligible"] is True and d["group_share"] == 0.28, d)
d2 = group_decision(items[:1], min_abs=0.2, min_share=0.3,
                    major_abs=1.0, group_share=0.25)
ok("单因低占比拒绝", d2["eligible"] is False, d2)
d3 = group_decision([], min_abs=0.2, min_share=0.3, major_abs=1.0,
                    group_share=0.25)
ok("无候选拒绝", d3["eligible"] is False, d3)
ov = [Contribution("hot_function:ChromaGame.Update", 0.8, 0.5),
      Contribution("hot_function:ChromaGame.UpdateBody", 0.3, 0.2)]
d4 = group_decision(ov, min_abs=0.2, min_share=0.3, major_abs=1.0,
                    group_share=0.25)
ok("父子去重", d4["group_share"] == 0.5, d4)

print(f"\n==== 多因判据测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
