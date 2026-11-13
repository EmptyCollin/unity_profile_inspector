"""I13a：两遍 replay 的窗口选择/编排（无 Unity）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.two_pass import (plan_deep_windows, run_two_pass,  # noqa: E402
                               select_windows)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


wins = [{"metric": f"m{i}", "start_frame": i * 100, "end_frame": i * 100 + 10,
         "score": 10 - i} for i in range(6)]
sel = select_windows(wins, max_n=3)
ok("最多选 3 个", len(sel) == 3, len(sel))
ok("按分数优先", [w.metric for w in sel] == ["m0", "m1", "m2"],
   [w.metric for w in sel])

dup = [{"metric": "m", "start_frame": 100, "end_frame": 110, "score": 1},
       {"metric": "m", "start_frame": 105, "end_frame": 130, "score": 2},
       {"metric": "m", "start_frame": 300, "end_frame": 310, "score": 0.5}]
sel2 = select_windows(dup, max_n=3, min_gap=30)
ok("同 metric 邻近合并", len(sel2) == 2, [(w.start_frame, w.end_frame) for w in sel2])
ok("合并取更广范围", sel2[0].start_frame == 100 and sel2[0].end_frame == 130,
   sel2[0].model_dump())

gap = [{"metric": "m0", "start_frame": 10, "end_frame": 20, "score": 5},
       {"metric": "m1", "start_frame": 100, "end_frame": 110, "score": 1,
        "coverage_gap": ["allocation", "loading"]}]
selg = select_windows(gap, max_n=1)
ok("覆盖缺口加权优先", selg[0].metric == "m1"
   and len(selg[0].coverage_gap) == 2, [w.metric for w in selg])

plan = plan_deep_windows([{"metric": "m", "start_frame": 50, "end_frame": 60,
                           "score": 1}], margin=10)
ok("窗口带余量", plan[0].start_frame == 40 and plan[0].end_frame == 70,
   plan[0].model_dump())

calls = []


def p1():
    return {"windows": wins}


def p2(selected):
    calls.append(selected)
    return {"deep": len(selected)}


res = run_two_pass(p1, p2, max_n=3, margin=5)
ok("r′ 收到选中窗口", res["status"] == "done" and len(calls[0]) == 3, res)
ok("r′ 只跑一次", len(calls) == 1)
ok("结果含两遍", res["pass1"] is not None and res["pass2"]["deep"] == 3)

calls.clear()
res2 = run_two_pass(lambda: {"windows": []}, p2)
ok("无窗口不跑 r′", res2["status"] == "no_window" and not calls, res2)

def boom(_w):
    raise RuntimeError("deep fail")


res3 = run_two_pass(p1, boom)
ok("r′ 失败 → unavailable", res3["status"] == "unavailable"
   and "deep fail" in res3["reason"], res3["reason"])

print(f"\n==== 两遍 replay 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
