"""I27 acceptance：relative/absolute/environment 三轴与状态组合。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.acceptance import absolute, decide, environment, relative, verdict  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


r = relative([10, 10, 10], [5, 5, 5])
ok("显著改善", r["status"] == "significant" and r["delta"] == 5, r)
ok("不显著", relative([10, 10, 10], [10.1, 10.1, 10.1])["status"]
   == "not_significant")
ok("劣化", relative([10, 10, 10], [20, 20, 20])["status"] == "regressed")

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

print(f"\n==== I27 acceptance: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
