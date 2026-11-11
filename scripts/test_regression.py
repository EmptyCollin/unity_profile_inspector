"""I4a：回归套件 / 3 次基线 / 复现率 / 回归计划 / verified 判定（离线）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E                       # noqa: E402
from upi.core.cluster import cluster_events            # noqa: E402
from upi.core.cluster import align_clusters, cluster_events  # noqa: E402
from upi.core.probe_events import EventLog, ProbeEvent   # noqa: E402
from upi.core.replay_driver import (ReplayDriverError,  # noqa: E402
                                    SubprocessReplayDriver, UnityReplayDriver,
                                    driver_for)
from upi.core.regression_report import (build_report, write_json_report,  # noqa: E402
                                        write_report)
from upi.core.regression import (BaselineMatrix, RegressionPlan,  # noqa: E402
                                 RegressionRun, RegressionRunner,
                                 RegressionStore, ReplaySuite, verify_after)

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_reg_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def cluster(metric="gc_alloc_per_frame", rel=10):
    e = ProbeEvent(kind="breach", metric=metric, rel_frame=rel,
                   value=2000.0, threshold=1500.0,
                   ring_before=[1000.0] * 5, ring_after=[2000.0] * 5,
                   data_tier="A2")
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

# 从当前事件记录 3 次基线 → 出计划
st2 = RegressionStore(TMP / "reg2")
suite2 = ReplaySuite(suite_id="s2", name="live", protocol_fingerprint="fp-live")
st2.save_suite(suite2)
log = EventLog(TMP / "ev.jsonl")
log.append(ProbeEvent(kind="breach", metric="gc_alloc_per_frame", rel_frame=10,
                      value=2000.0, threshold=1500.0,
                      ring_before=[1000.0] * 5, ring_after=[2000.0] * 5,
                      data_tier="A2"))
for _ in range(3):
    st2.record_run("s2", log)
sm = st2.summary("s2")
ok("3 次记录进矩阵", sm["runs"] == 3 and sm["protocol_fingerprint"] == "fp-live")
ok("3 次后计划 ready", sm["plan"]["status"] == "ready"
   and len(sm["plan"]["items"]) == 1)
ok("套件列表", any(x.suite_id == "s2" for x in st2.list_suites()))

# 可注入 replay runner：3 次基线 + 守护
st5 = RegressionStore(TMP / "reg5")
suite5 = ReplaySuite(suite_id="s5", protocol_fingerprint="fp-run")
def run_once(_suite):
    return "fp-run", [ProbeEvent(kind="breach", metric="gc_alloc_per_frame",
                                 rel_frame=10, value=2000.0, threshold=1500.0,
                                 ring_before=[1000.0] * 5,
                                 ring_after=[2000.0] * 5, data_tier="A2")]
runner = RegressionRunner(suite5, st5, run_once)
m5 = runner.collect_baseline(3)
ok("runner 采 3 次", len(m5.runs) == 3)
ok("runner 采守护", m5.runs[0].guards.get("gc_alloc_per_frame") == 2000.0)
ok("runner 计划 ready", RegressionPlan.from_matrix(m5).status == "ready")

# 第 4 次（修复后）→ verified 账本
st3 = RegressionStore(TMP / "reg3")
base_runs = [RegressionRun(run_id=f"v{i}", suite_id="s3", run_index=i,
                           protocol_fingerprint="fp-v",
                           clusters=[A, B]) for i in range(3)]
after_ok = RegressionRun(run_id="v3", suite_id="s3", run_index=3,
                         protocol_fingerprint="fp-v", clusters=[])
st3.save_matrix(BaselineMatrix(suite_id="s3", runs=base_runs + [after_ok]))
res = st3.verify_last("s3")
ok("第 4 次目标消失→verified", res["ok"] and res["verified"]
   and res["record"]["verdict"]["verification_status"] == "verified")
ok("verified 账本落盘", len(st3.list_verdicts("s3")) == 1)
ok("summary 带 last_verdict", st3.summary("s3")["last_verdict"]["status"] == "verified")
rep = build_report(st3, "s3")
ok("报告含 plan/verdict", rep["plan"] is not None and rep["verified_count"] == 1)
ok("报告带形态/验证等级", rep["mode"] == "regression"
   and rep["verification_status"] == "verified")
ok("报告带不可检出清单（G20）", len(rep["blind_spots"]) == 5)
rp = write_report(st3, "s3")
txt = rp.read_text(encoding="utf-8")
ok("报告落盘", rp.exists() and "回归报告" in txt)
ok("HTML 含 G20 清单", "不可检出清单" in txt and "gpu_attribution" in txt)
jp = write_json_report(st3, "s3")
ok("JSON 报告落盘", jp.exists() and jp.read_text(encoding="utf-8").find('"mode": "regression"') >= 0)

st4 = RegressionStore(TMP / "reg4")
after_bad = RegressionRun(run_id="v3", suite_id="s4", run_index=3,
                          protocol_fingerprint="fp-v", clusters=[A, B])
st4.save_matrix(BaselineMatrix(suite_id="s4", runs=[
    RegressionRun(run_id=f"w{i}", suite_id="s4", run_index=i,
                  protocol_fingerprint="fp-v", clusters=[A, B]) for i in range(3)
] + [after_bad]))
res2 = st4.verify_last("s4")
ok("目标仍在→不 verified", res2["ok"] and not res2["verified"]
   and res2["record"]["verdict"]["status"] == "target_present"
   and len(st4.list_verdicts("s4")) == 1)
ok("失败 verdict 也审计", st4.list_verdicts("s4")[0].verdict.verification_status
   == "unverified")

# 时间原点对齐
sh = cluster_events([ProbeEvent(kind="sample", metric="m1", rel_frame=100,
                                series=[1.0] * 5, data_tier="A2"),
                     ProbeEvent(kind="sample", metric="m2", rel_frame=130,
                                series=[2.0] * 5, data_tier="A2")])
al = align_clusters(sh)
ok("对齐最早 cluster 到 0", al[0].first_rel == 0 and al[1].first_rel == 30)
ok("对齐保留 cluster_id", al[0].cluster_id == sh[0].cluster_id)
ok("对齐 occurrence 同步", al[0].occurrences[0].rel_frame == 0)

# 子进程 driver：执行命令后读事件
log2 = EventLog()
log2.append(ProbeEvent(kind="sample", metric="m1", rel_frame=1,
                       series=[1.0] * 5, data_tier="A2"))
suite_d = ReplaySuite(suite_id="sd", project=str(TMP), command=f"{sys.executable} -c pass")
drv = SubprocessReplayDriver()
proto, evs = drv.run_once(suite_d)
ok("subprocess driver 读到事件", proto == "" and len(evs) >= 1)
try:
    drv.run_once(ReplaySuite(suite_id="empty", command=""))
    ok("空命令报错", False)
except ReplayDriverError:
    ok("空命令报错", True)

# 自动 Unity replay：命令名/探针开关/新增事件（全注入，不碰 Editor）
class _Probe:
    def __init__(self):
        self.calls = []

    def sample(self, on, window=300):
        self.calls.append((on, window))
        return {"ok": True, "detail": "sample=" + ("on" if on else "off")}


_ulog = EventLog()
_ucli_calls = []


def _ucli(name, *extra, project=None):
    _ucli_calls.append(name)
    if name == "editor.playmode start":
        _ulog.append(ProbeEvent(kind="sample", metric="cpu", rel_frame=1,
                                series=[1.0] * 5, data_tier="A2"))
    return {"ok": True, "data": {}}


_uprobe = _Probe()
_udrv = UnityReplayDriver(cli=_ucli, probe=_uprobe, sleep=lambda _s: None,
                          event_log=_ulog, warmup_s=0, window_s=0.01)
_usuite = ReplaySuite(suite_id="u", project=str(TMP),
                      scene="Assets/UpiFixtures/Scenes/X.unity",
                      protocol_fingerprint="fp-u")
_uproto, _uevs = _udrv.run_once(_usuite)
ok("Unity driver 返回协议与新增事件", _uproto == "fp-u" and len(_uevs) >= 1,
   f"{_uproto} n={len(_uevs)}")
ok("Unity driver 开场景/Play/Stop 全走 CLI",
   "scene open" in _ucli_calls and "editor.playmode start" in _ucli_calls
   and "editor.playmode stop" in _ucli_calls, str(_ucli_calls))
ok("Unity driver 采样开+关", [c[0] for c in _uprobe.calls] == [True, False],
   str(_uprobe.calls))
ok("driver_for 选 Unity（无 command 有 scene）",
   isinstance(driver_for(_usuite), UnityReplayDriver))
ok("driver_for 选子进程（有 command）",
   isinstance(driver_for(ReplaySuite(suite_id="s", command="x")),
              SubprocessReplayDriver))

# sample 窗口按指标合并：多次抽检不是多个 issue
_me = [ProbeEvent(kind="sample", metric="m", rel_frame=r,
                  series=[1.0] * 5, data_tier="A2") for r in (1, 2, 3)]
ok("sample 合并为单 cluster",
   len(cluster_events(_me, merge_gap=300, merge_sample=True)) == 1)


print()
if FAIL:
    print(f"==== 回归形态测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== 回归形态测试: {len(PASS)} 通过, 0 失败")
