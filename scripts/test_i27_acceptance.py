"""I27 acceptance：relative/absolute/environment 三轴与状态组合。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.acceptance import (absolute, decide, environment,  # noqa: E402
                                 group_by_overlap, merge_window_verdicts,
                                 min_ratio_for, relative, status_compare,
                                 verdict)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


r = relative([10, 10, 10], [5, 5, 5])
ok("显著改善", r["status"] == "significant" and r["delta"] == 5, r)
ok("不显著", relative([10, 10, 10], [10.1, 10.1, 10.1])["status"]
   == "not_significant")
ok("劣化", relative([10, 10, 10], [20, 20, 20])["status"] == "regressed")
ok("离群值不放大阈值",
   relative([10, 10, 10, 1000], [5, 5, 5, 1000])["status"]
   == "significant",
   relative([10, 10, 10, 1000], [5, 5, 5, 1000]))

a_met = absolute(5, 6)
a_unmet = absolute(5, 4)
ok("绝对达线", a_met["status"] == "met", a_met)
ok("绝对未达线", a_unmet["status"] == "unmet", a_unmet)
ok("无目标 unassessed", absolute(5, None)["status"] == "unassessed")

e_ok = environment([10, 10, 10], [10.1, 10.1, 10.1])
e_drift = environment([10, 10, 10], [14, 14, 14])
ok("control 稳定", e_ok["status"] == "ok", e_ok)
ok("control 漂移", e_drift["status"] == "drift", e_drift)

ok("verified", decide(rel=r, abs_=a_met, env=e_ok) == "verified")
ok("相对改善但绝对未达线",
   decide(rel=r, abs_=a_unmet, env=e_ok) == "improved_partial_absolute_unmet")
ok("绝对未评估",
   decide(rel=r, abs_=absolute(5, None), env=e_ok)
   == "improved_partial_absolute_unassessed")
ok("环境漂移优先",
   decide(rel=r, abs_=a_met, env=e_drift) == "environment_drift")
ok("语义风险优先",
   decide(rel=r, abs_=a_met, env=e_ok, semantic_risk="empty_replace")
   == "semantic_risk")
ok("不显著",
   decide(rel=relative([10, 10], [10, 10]), abs_=a_met, env=e_ok)
   == "not_significant")

v = verdict("u1", before=[10, 10, 10], after=[5, 5, 5],
            control_before=[10, 10, 10], control_after=[10, 10, 10],
            absolute_target=6)
ok("verdict verified", v.status == "verified", v)
v2 = verdict("u2", before=[10, 10, 10], after=[5, 5, 5],
             control_before=[10, 10, 10], control_after=[14, 14, 14],
             absolute_target=6)
ok("verdict environment_drift", v2.status == "environment_drift", v2)

items = [
    {"id": "a", "window_ids": ["cmd-1-x"], "files": ["A.cs"]},
    {"id": "b", "window_ids": ["cmd-1-x"], "files": ["B.cs"]},
    {"id": "c", "window_ids": ["cmd-2-y"], "files": ["B.cs"]},
    {"id": "d", "window_ids": ["cmd-3-z"], "files": ["C.cs"]},
]
groups = group_by_overlap(items)
ok("window 相交合并", any({x["id"] for x in g} == {"a", "b", "c"}
                         for g in groups), groups)
ok("文件相交合并", any({"b", "c"} <= {x["id"] for x in g} for g in groups), groups)
ok("不相交独立", any({x["id"] for x in g} == {"d"} for g in groups), groups)
no_overlap = group_by_overlap([
    {"id": "a", "window_ids": ["cmd-1-x"], "files": ["A.cs"]},
    {"id": "b", "window_ids": ["cmd-2-y"], "files": ["B.cs"]},
])
ok("无交集不合并", len(no_overlap) == 2, no_overlap)

cmp = status_compare([
    {"status": "needs_recheck", "acceptance": {"status": "verified"}},
    {"status": "unmeasured", "acceptance": {"status": "unassessed"}},
    {"status": "verified", "acceptance": {"status": "verified"}},
])
ok("status_compare n", cmp["n"] == 2, cmp)
ok("status_compare changed", cmp["changed"] == 1, cmp)
ok("status_compare 跳 unassessed", "unmeasured -> unassessed" not in cmp["pairs"], cmp)

from upi.core.finding import dedupe_hypotheses  # noqa: E402
hs = [
    {"root_cause": "a", "window_id": "w1", "finding_metric": "m",
     "confidence": 0.4},
    {"root_cause": "a", "window_id": "w1", "finding_metric": "m",
     "confidence": 0.8},
    {"root_cause": "a", "window_id": "w2", "finding_metric": "m",
     "confidence": 0.3},
]
dh = dedupe_hypotheses(hs)
ok("hypothesis 去重", len(dh) == 2, dh)
ok("hypothesis 留最高置信", dh[0]["confidence"] == 0.8, dh)

ok("frame_time 3%", min_ratio_for("frame_time") == 0.03)
ok("gc_alloc 10%", min_ratio_for("gc_alloc") == 0.10)
ok("默认 5%", min_ratio_for("other") == 0.05)
v3 = verdict("u3", before=[100, 100, 100], after=[96, 96, 96],
             metric="frame_time")
ok("按 metric 取 min_ratio",
   v3.relative.get("min_ratio") == 0.03
   and v3.relative["status"] == "significant", v3.relative)
ok("frame_time 显著但无绝对 target",
   v3.status == "improved_partial_absolute_unassessed", v3.status)

wrecs = [
    {"unit_id": "a", "window": "cmd-10-open_shop",
     "status": "verified", "acceptance": {"status": "verified"}},
    {"unit_id": "b", "window": "cmd-10-open_shop",
     "status": "not_significant",
     "acceptance": {"status": "not_significant"}},
]
wv = merge_window_verdicts(wrecs)
ok("同窗合并取最差",
   wv["cmd-10-open_shop"]["status"] == "not_significant", wv)
ok("同窗记录 window_status",
   all(r.get("window_status") == "not_significant" for r in wrecs), wrecs)

print(f"\n==== I27 acceptance: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
