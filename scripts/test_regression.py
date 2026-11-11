"""I4a：回归套件 / 3 次基线 / 复现率 / 回归计划 / verified 判定（离线）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E                       # noqa: E402
from upi.core.cluster import cluster_events            # noqa: E402
from upi.core.probe_events import ProbeEvent           # noqa: E402
from upi.core.regression import (BaselineMatrix, RegressionPlan,  # noqa: E402
                                 RegressionRun, RegressionStore,
                                 ReplaySuite, verify_after)

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_reg_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def cluster(metric="gc_alloc_per_frame", rel=10):
    e = ProbeEvent(kind="sample", metric=metric, rel_frame=rel,
                   series=[1000.0] * 20, data_tier="A2")
    return cluster_events([e])[0]


A = cluster("gc_alloc_per_frame", 10)
B = cluster("drawCalls", 100)
C = cluster("frame_time_ms", 50)

suite = ReplaySuite(suite_id="s1", name="t", project="p", scene="s",
                    replay_script="r", protocol_fingerprint="fp-1")
runs = [RegressionRun(run_id=f"r{i}", suite_id="s1", run_index=i,
                      protocol_fingerprint="fp-1",
                      clusters=[A, B] if i == 0 else [A],
                      event_count=10) for i in range(3)]
m = BaselineMatrix(suite_id="s1", runs=runs)
ms = m.matches()
a = [x for x in ms if x.cluster.metric == "gc_alloc_per_frame"][0]
b = [x for x in ms if x.cluster.metric == "drawCalls"][0]
ok("A 复现 3/3", a.reproduction_rate == 1.0 and a.is_stable)
ok("B 复现 1/3", b.reproduction_rate == round(1 / 3, 3) and not b.is_stable)
plan = RegressionPlan.from_matrix(m)
ok("计划只收 A", plan.status == "ready" and len(plan.items) == 1
   and plan.items[0].metric == "gc_alloc_per_frame")
ok("计划带复现率", plan.items[0].reproduction_rate == 1.0)

# 目标消失 + 无新 cluster + 守护不劣化 → verified
v = verify_after(m, A, [B], after_protocol="fp-1",
                 guards_before={"drawCalls": 47}, guards_after={"drawCalls": 47})
ok("目标消失→verified", v.status == "verified" and v.verification_status == "verified")

# 目标仍在 → not verified
v2 = verify_after(m, A, [A, B], after_protocol="fp-1")
ok("目标仍在→target_present", v2.status == "target_present"
   and v2.verification_status == "unverified")

# 新 cluster → 不 verified
v3 = verify_after(m, A, [B, C], after_protocol="fp-1")
ok("新 cluster→new_cluster", v3.status == "new_cluster" and C.cluster_id in v3.new_clusters)

# 守护劣化 → 不 verified
v4 = verify_after(m, A, [B], after_protocol="fp-1",
                  guards_before={"drawCalls": 100}, guards_after={"drawCalls": 102})
ok("守护劣化→guard_regressed", v4.status == "guard_regressed"
   and "drawCalls" in v4.guard_regressions)

# 协议不一致 → failed
v5 = verify_after(m, A, [B], after_protocol="fp-2")
ok("协议不一致→failed", v5.status == "protocol_mismatch"
   and v5.verification_status == "failed")

# 不足 3 次 → 不出计划
m2 = BaselineMatrix(suite_id="s1", runs=runs[:2])
p2 = RegressionPlan.from_matrix(m2)
ok("不足 3 次→insufficient", p2.status == "insufficient_runs" and not p2.items)

# 持久化
st = RegressionStore(TMP / "reg")
st.save_suite(suite)
st.save_matrix(m)
ok("套件持久化", st.load_suite("s1").protocol_fingerprint == "fp-1")
ok("矩阵持久化", st.load_matrix("s1").runs[0].run_id == "r0")

print()
if FAIL:
    print(f"==== 回归形态测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== 回归形态测试: {len(PASS)} 通过, 0 失败")
