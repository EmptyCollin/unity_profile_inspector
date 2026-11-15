"""I26 P4：RepairCandidate 与语义风险。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_candidate import (RepairCandidate,  # noqa: E402
                                       metric_allowed, rank_candidates,
                                       semantic_risk_for_diffs)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


good = RepairCandidate("alloc", "A.cs::Update:10", "gc_alloc", "open_shop",
                       expected_saved_bytes=4096, evidence_refs=["e1"])
ok("完整候选通过", good.validate() == [], good.validate())

bad = RepairCandidate("alloc", "", "", "", evidence_refs=[])
errs = bad.validate()
ok("缺字段拒绝", "missing_site" in errs and "missing_metric" in errs
   and "missing_window" in errs and "missing_evidence" in errs, errs)

risk = RepairCandidate("delete", "A::B:1", "frame_time", "open_shop",
                       expected_saved_ms=2, evidence_refs=["e1"],
                       semantic_risk="empty_replace")
ok("语义风险需测试", "semantic_risk_without_test" in risk.validate(),
   risk.validate())

ok("空 replace 风险",
   semantic_risk_for_diffs([{"find": "x", "replace": ""}]) == "empty_replace")
ok("大块删除风险",
   semantic_risk_for_diffs([{"find": "x" * 500, "replace": "y"}]) == "block_delete")
ok("正常 patch 无风险",
   semantic_risk_for_diffs([{"find": "new X()", "replace": "cache"}]) == "")

low = RepairCandidate("a", "s1", "m", "w", expected_saved_ms=1,
                      evidence_refs=["e"])
high = RepairCandidate("a", "s2", "m", "w", expected_saved_ms=5,
                       evidence_refs=["e"])
ok("按收益排序", rank_candidates([low, high])[0] is high)
ok("allocation 允许 gc_alloc",
   metric_allowed("allocation", "gc_alloc") is True)
ok("allocation 拒绝 drawcalls",
   metric_allowed("allocation", "drawcalls") is False)
ok("sync 允许 drawcalls", metric_allowed("sync", "drawcalls") is True)

print(f"\n==== I26 候选测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
