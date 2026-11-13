"""I3a：批量工单 / 串行执行 / 审批 / 回滚 / unverified 账本（离线）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from types import SimpleNamespace  # noqa: E402

from upi.core import events as E                       # noqa: E402
from upi.core.cluster import cluster_events            # noqa: E402
from upi.core.models import EditPlan                   # noqa: E402
from upi.core.probe_events import EventLog, ProbeEvent  # noqa: E402
from upi.core.resident import ResidentEngine           # noqa: E402
from upi.core.ticket import (TicketExecutor, TicketStore, approve,  # noqa: E402
                             approve_item, build_ticket, reject)


class FakeFp:
    """不预置指纹（I10）：测试直接给“已学习命中”。"""
    def route(self, features):
        pb = ("PB-SHARED-MAT-MPB" if getattr(features, "metric", "")
              == "drawCalls" else "PB-REUSE-BUFFER")
        return SimpleNamespace(hit=True, llm_required=False,
                               record=SimpleNamespace(
                                   fingerprint_id="fp-test",
                                   pattern_id="learned:test",
                                   playbook_id=pb,
                                   root_cause="learned"))

    def record_outcome(self, *a, **k):
        return None

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_ticket_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def sample(rel: int, metric="gc_alloc_per_frame", tier="A2", n=20):
    return ProbeEvent(kind="sample", metric=metric, rel_frame=rel,
                      series=[1000.0] * n, data_tier=tier)


def plan():
    return EditPlan(playbook_id="PB-REUSE-BUFFER", diffs=[{"file": "x.cs"}],
                    side_effect="none", permission_tier="auto")


fp = FakeFp()
store = TicketStore(TMP / "tickets")

cs = cluster_events([sample(10), sample(100)], merge_gap=30)
t = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)
ok("批量工单容纳多个候选", len(t.items) == 2 and t.status == "awaiting_approval")
ok("auto playbook 进入 pending", all(i.status == "pending" for i in t.items))
ok("指纹命中写入 pattern", all(i.fingerprint_hit and i.pattern_id for i in t.items))
ok("批准校验码 4 位", len(t.approve_token) == 4)
store.save(t)

# 审批门禁
ok("空批准人被拒", not approve(t, approver="", note="ok", token=t.approve_token)[0])
ok("错校验码被拒", not approve(t, approver="我", note="ok", token="0000")[0])
ok("正确批准通过", approve(t, approver="我", note="批准", token=t.approve_token)[0]
   and t.status == "approved")
ok("审批历史留痕", any(h.get("action") == "approve_ticket" for h in t.history))

# 串行执行成功
applied, rolled = [], []
ex = TicketExecutor(store, apply=lambda p: applied.append(p.playbook_id),
                    compile_ok=lambda: True,
                    observe=lambda: {"sample": 1},
                    rollback=lambda: rolled.append(1))
out = ex.execute(t)
ok("执行后状态 done", out.status == "done")
ok("两个 patch 都 applied_unverified",
   all(i.status == "applied_unverified" for i in out.items))
ok("执行一次一个（顺序）", applied == ["PB-REUSE-BUFFER", "PB-REUSE-BUFFER"])
ok("未触发回滚", rolled == [])
ok("留痕含 observation", out.items[0].observation == {"sample": 1})

# 编译失败：回滚并停止后续
cs2 = cluster_events([sample(10), sample(200)], merge_gap=30)
t2 = build_ticket(cs2, fp_store=fp, plan_provider=lambda c, pb: plan(), limit=8)
approve(t2, approver="我", note="批", token=t2.approve_token)
applied2, rolled2 = [], []
ex2 = TicketExecutor(store, apply=lambda p: applied2.append(1),
                     compile_ok=lambda: False,
                     observe=lambda: {},
                     rollback=lambda: rolled2.append(1))
out2 = ex2.execute(t2)
ok("编译失败回滚", len(rolled2) == 1 and out2.items[0].status == "rolled_back")
ok("失败后停止后续", out2.items[1].status == "skipped")
ok("只应用了一个 patch", len(applied2) == 1)

# 无 plan / 非 auto：needs_human，不执行
t3 = build_ticket(cs, fp_store=fp, plan_provider=None)
ok("无 plan 标 needs_human", all(i.status == "needs_human" for i in t3.items)
   and "provider" in t3.items[0].error)
cs_draw = cluster_events([sample(10, metric="drawCalls")], merge_gap=30)
t4 = build_ticket(cs_draw, fp_store=fp, plan_provider=lambda c, pb: plan())
ok("semi 不自动执行", t4.items[0].status == "needs_human"
   and t4.items[0].permission_tier == "semi")

# 驳回
t5 = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: plan())
ok("驳回", reject(t5, approver="我", note="不修")[0] and t5.status == "rejected")

# blocked plan：批量批准不够，必须单条批准
def blocked_plan():
    return EditPlan(playbook_id="PB-FULL-REUSE", diffs=[{"file": "x.cs"}],
                    side_effect="none", permission_tier="blocked")


t7 = build_ticket(cs, fp_store=fp, plan_provider=lambda c, pb: blocked_plan())
it = t7.items[0]
ok("blocked plan 初始 needs_human", it.status == "needs_human"
   and it.permission_tier == "blocked" and it.plan is not None)
approve(t7, approver="我", note="批工单", token=t7.approve_token)
ok("批量批准不自动执行 blocked", it.status == "needs_human")
ok("错单条码被拒", not approve_item(t7, it.item_id, approver="我", note="已读",
                                  token="0000")[0])
ok("单条批准通过", approve_item(t7, it.item_id, approver="我", note="已读计划",
                              token=it.item_approve_token)[0]
   and it.status == "pending" and it.human_approved)
ok("明细带可展开 diff", len(t7.summary()["item_list"][0]["plan_diffs"]) >= 1)
applied7 = []
out7 = TicketExecutor(store, apply=lambda p: applied7.append(p.playbook_id),
                      compile_ok=lambda: True, observe=lambda: {},
                      rollback=lambda: None).execute(t7)
ok("单条批准后可执行", out7.items[0].status == "applied_unverified"
   and applied7 == ["PB-FULL-REUSE"])

# 持久化 + ResidentEngine 装配
store.save(t5)
loaded = TicketStore(TMP / "tickets").load(t5.ticket_id)
ok("工单持久化", loaded is not None and loaded.ticket_id == t5.ticket_id
   and len(loaded.items) == len(t5.items))
ok("工单列表", len(TicketStore(TMP / "tickets").list()) >= 3)

log = EventLog(TMP / "probe_events.jsonl")
log.append(sample(10))
engine = ResidentEngine(event_log=log, fp_store=fp, ticket_store=store)
rt = engine.build_ticket(plan_provider=lambda c, pb: plan())
ok("ResidentEngine 从事件建单", rt.status == "awaiting_approval" and len(rt.items) == 1)

print()
if FAIL:
    print(f"==== 工单/执行器测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== 工单/执行器测试: {len(PASS)} 通过, 0 失败")
