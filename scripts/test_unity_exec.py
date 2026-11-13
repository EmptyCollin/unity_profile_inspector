"""I3b-2：Unity 执行适配（离线，注入假 snapshot/execute/observe）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from types import SimpleNamespace  # noqa: E402

from upi.core import events as E                          # noqa: E402
from upi.core.cluster import cluster_events                # noqa: E402
from upi.core.models import EditPlan                       # noqa: E402
from upi.core.probe_events import ProbeEvent               # noqa: E402
from upi.core.ticket import (TicketStore, approve, approve_item,  # noqa: E402
                             build_ticket)
from upi.core.unity_exec import UnityTicketExecutor        # noqa: E402


class FakeFp:
    def route(self, features):
        return SimpleNamespace(hit=True, llm_required=False,
                               record=SimpleNamespace(
                                   fingerprint_id="fp-test",
                                   pattern_id="learned:test",
                                   playbook_id="PB-REUSE-BUFFER",
                                   root_cause="learned"))

    def record_outcome(self, *a, **k):
        return None

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_unity_exec_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def sample(rel, metric="gc_alloc_per_frame"):
    return ProbeEvent(kind="sample", metric=metric, rel_frame=rel,
                      series=[1000.0] * 20, data_tier="A2")


def plan():
    return EditPlan(playbook_id="PB-REUSE-BUFFER", diffs=[{"file": "x.cs"}],
                    side_effect="none", permission_tier="auto")


fp = FakeFp()
store = TicketStore(TMP / "tickets")
cs = cluster_events([sample(10), sample(100)], merge_gap=30)
t = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)


def approved(ticket):
    approve(ticket, approver="我", note="批", token=ticket.approve_token)
    return ticket


# 成功：snapshot 先于 execute，observe 留痕
order = []


def snap(*a):
    order.append("snapshot")
    return object()


def ex(plan, snap, **kw):
    order.append("execute")
    return {"status": "applied"}


def obs():
    return {"events": 1, "verification": "unverified"}


approved(t)
out = UnityTicketExecutor(store, snapshot_fn=snap, execute_fn=ex, observe_fn=obs,
                           project=TMP, scene=TMP / "s.tscene").execute(t)
ok("执行 done", out.status == "done")
ok("成功只 applied_unverified", all(i.status == "applied_unverified" for i in out.items))
ok("snapshot 先于 execute", order == ["snapshot", "execute", "snapshot", "execute"])
ok("observation 留痕", out.items[0].observation["verification"] == "unverified")

# 编译失败：rolled_back + 后续 skipped
t2 = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)
approved(t2)
called = []


def ex_bad(plan, snap, **kw):
    called.append(1)
    return {"status": "compile_failed_rolled_back"}


out2 = UnityTicketExecutor(store, snapshot_fn=snap, execute_fn=ex_bad,
                            observe_fn=obs, project=TMP,
                            scene=TMP / "s.tscene").execute(t2)
ok("编译失败 rolled_back", out2.items[0].status == "rolled_back")
ok("编译失败不阻断后续（I11b）", out2.items[1].status in
   ("applied_unverified", "rolled_back"))
ok("两条都执行（没有 stopped）", len(called) == 2)

# 编译失败 → 回喂重规划一次 → 第二次成功（resident 上限 1）
t2b = build_ticket(cs[:1], fp_store=fp, plan_provider=lambda c, pb: plan())
approved(t2b)
tries = []
replans = []


def ex_retry(plan, snap, **kw):
    tries.append(1)
    return ({"status": "compile_failed_rolled_back"} if len(tries) == 1
            else {"status": "applied"})


def replan(item, plan, err):
    replans.append(err)
    return plan


out2b = UnityTicketExecutor(store, snapshot_fn=snap, execute_fn=ex_retry,
                            observe_fn=obs, project=TMP,
                            scene=TMP / "s.tscene", replan=replan).execute(t2b)
ok("编译失败回喂重规划后成功",
   out2b.items[0].status == "applied_unverified" and len(tries) == 2,
   (out2b.items[0].status, len(tries)))
ok("重规划被调用一次", len(replans) == 1, replans)

# execute 抛异常：failed + 停止
t3 = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)
approved(t3)


def ex_exc(plan, snap, **kw):
    raise RuntimeError("boom")


out3 = UnityTicketExecutor(store, snapshot_fn=snap, execute_fn=ex_exc,
                            observe_fn=obs, project=TMP,
                            scene=TMP / "s.tscene").execute(t3)
ok("异常 failed", out3.items[0].status == "failed" and "boom" in out3.items[0].error)
ok("异常后停止", out3.items[1].status == "skipped")

# 无 scene：拒绝执行
t4 = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)
approved(t4)
ex4 = UnityTicketExecutor(store, project=TMP, scene=TMP / "s.tscene")
ex4.scene = None
out4 = ex4.execute(t4)
ok("无 scene 拒绝", out4.status == "done" and any(
    ("scene" in str(r.get("error", ""))) or ("场景" in str(r.get("error", "")))
    for r in out4.results))

# 未批准
t5 = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)
out5 = UnityTicketExecutor(store, scene=TMP / "s.tscene", project=TMP).execute(t5)
ok("未批准拒绝", out5.status == "awaiting_approval"

   and any("未批准" in str(r.get("error", "")) for r in out5.results))

# 无 plan 不执行
t6 = build_ticket(cs, fp_store=fp, plan_provider=None)
approved(t6)
out6 = UnityTicketExecutor(store, scene=TMP / "s.tscene", project=TMP).execute(t6)
ok("无 plan 不执行", all(i.status == "needs_human" for i in out6.items))

# blocked plan：单条批准后 Unity 适配才执行
def blocked():
    return EditPlan(playbook_id="PB-FULL-REUSE", diffs=[{"file": "x.cs"}],
                    side_effect="none", permission_tier="blocked")


tb = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: blocked(), limit=8)
approve(tb, approver="我", note="批", token=tb.approve_token)
itb = tb.items[0]
ok("blocked 初始不执行", itb.status == "needs_human")
approve_item(tb, itb.item_id, approver="我", note="已读", token=itb.item_approve_token)
outb = UnityTicketExecutor(store, snapshot_fn=snap, execute_fn=ex, observe_fn=obs,
                            project=TMP, scene=TMP / "s.tscene").execute(tb)
ok("单条批准后 Unity 适配执行", outb.items[0].status == "applied_unverified")

print()
if FAIL:
    print(f"==== Unity 执行适配测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== Unity 执行适配测试: {len(PASS)} 通过, 0 失败")
