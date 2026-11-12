"""学习闭环三块机制（离线）：影子一致率 / 假设台账 / 协议指纹。

覆盖：
- 影子模式：pattern 升入 shadow 后，即使 plan 可自动也只记意图、必须人工；
  人工批准/驳回与自动意图的比对落入 shadow_decisions。
- 假设台账：Top1 记 adopted、其余记 not_adopted；结局只回填 Top1；
  ECE 只收有真实结局的样本，且覆盖率必须与 ECE 同屏。
- 协议指纹：同输入稳定、改档位即不同；账本 outcome 落指纹；
  回归判定的 protocol_fingerprint 不再为空。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E                      # noqa: E402
from upi.core.cluster import cluster_events           # noqa: E402
from upi.core.knowledge import KnowledgeStore, default_path  # noqa: E402
from upi.core.models import EditPlan                  # noqa: E402
from upi.core.probe_events import ProbeEvent          # noqa: E402
from upi.core.protocol import build, digest, file_hash, comparable  # noqa: E402
from upi.core.regression import (BaselineMatrix, RegressionRun,  # noqa: E402
                                 verify_after)
from upi.core.ticket import (BatchTicket, TicketStore, approve_item,  # noqa: E402
                             build_ticket, reject)

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def cluster(metric="gc_alloc_per_frame", rel=10):
    e = ProbeEvent(kind="breach", metric=metric, rel_frame=rel,
                   value=2000.0, threshold=1500.0,
                   ring_before=[1000.0] * 5, ring_after=[2000.0] * 5,
                   data_tier="A2")
    return cluster_events([e])[0]


class FakeFpStore:
    def __init__(self, pattern_id="pat-1", playbook="PB-REUSE-BUFFER"):
        self.pattern_id, self.playbook = pattern_id, playbook

    def route(self, _features):
        return SimpleNamespace(hit=True,
                               record=SimpleNamespace(pattern_id=self.pattern_id,
                                                      playbook_id=self.playbook))


def auto_plan(_c, playbook):
    return EditPlan(playbook_id=playbook,
                    diffs=[{"file": "A.cs", "find": "a", "replace": "b"}],
                    side_effect="none", touched_assets=["A.cs"],
                    permission_tier="auto")


tmp = Path(tempfile.mkdtemp(prefix="upi_learn_"))
ks = KnowledgeStore(tmp / "patterns.db")
ks.upsert_pattern("pat-1", "gc_alloc_per_frame", "cpu",
                  "每帧重建 HUD 文本", "PB-REUSE-BUFFER",
                  playbook_id="PB-REUSE-BUFFER")

# ── 影子模式 ─────────────────────────────────────────────
t1 = build_ticket([cluster()], fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks)
item = t1.items[0]
ok("semi 档：可自动的候选直接 pending（影子未启用）",
   item.pattern_tier == "semi" and not item.shadow
   and item.status == "pending" and item.shadow_auto)

ks.set_tier("pat-1", "shadow", actor="tester", reason="promote")
ok("升档留痕", ks.tier_of("pat-1") == "shadow")

t2 = build_ticket([cluster()], fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks)
i2 = t2.items[0]
ok("shadow 档：不再直接 pending，必须人工", i2.shadow and i2.status == "needs_human")
ok("shadow 档：仍记录'自动会 apply'", i2.shadow_auto is True
   and "shadow" in i2.error)
ok("shadow 档：plan 仍然生成（供人审）", i2.plan is not None)

t2.status = "approved"
good, msg = approve_item(t2, i2.item_id, approver="tester", note="同意",
                         token=i2.item_approve_token, knowledge_store=ks)
ag = ks.shadow_agreement()
ok("人工批准 → 影子一致", good and ag["n_decided"] == 1 and ag["n_agreed"] == 1
   and ag["rate"] == 1.0, f"{good} {msg} {ag}")

t3 = build_ticket([cluster(rel=20)], fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks)
t3.status = "awaiting_approval"
good, msg = reject(t3, approver="tester", note="不该自动", knowledge_store=ks)
ag2 = ks.shadow_agreement()
ok("人工驳回 → 记不一致（自动想 apply，人却拒绝）",
   good and ag2["n_decided"] == 2 and ag2["n_agreed"] == 1, f"{ag2}")

# ── 假设台账 ─────────────────────────────────────────────
n = ks.record_hypotheses("run-1", "det-1:10",
                         [{"root_cause": "hud_text", "confidence": 0.8},
                          {"root_cause": "alloc_site", "confidence": 0.5},
                          {"root_cause": "other", "confidence": 0.2}])
cov0 = ks.ece_coverage()
ok("三个假设全部入账", n == 3 and cov0["n_hypotheses"] == 3)
ok("落选假设记 not_adopted（不是失败）",
   cov0["n_not_adopted"] == 2 and cov0["n_with_outcome"] == 0)
ks.record_hypothesis_outcome("det-1:10", "resolved")
cov1 = ks.ece_coverage()
pairs = ks.ece_pairs()
ok("结局只回填 Top1", cov1["n_with_outcome"] == 1 and len(pairs) == 1
   and pairs[0]["success"] == 1.0)
ok("ECE 覆盖率与样本同屏", cov1["coverage"] == round(1 / 3, 6)
   or abs(cov1["coverage"] - 1 / 3) < 1e-6)

# ── 协议指纹 ─────────────────────────────────────────────
fp1 = build(scene="S1", quality_tier="high", data_tier="A2",
            replay_script_hash="sha256:abc", poll_rate=1.0)
fp2 = build(scene="S1", quality_tier="high", data_tier="A2",
            replay_script_hash="sha256:abc", poll_rate=1.0)
fp3 = build(scene="S1", quality_tier="low", data_tier="A2",
            replay_script_hash="sha256:abc", poll_rate=1.0)
ok("同输入指纹稳定", digest(fp1) == digest(fp2))
ok("换档位即不可比", digest(fp1) != digest(fp3) and not comparable(fp1, fp3))
ok("缺失回放脚本如实标 none-v0",
   file_hash(tmp / "nope.json") == "none-v0")

ks.record_outcome("pat-1", "run-1", "fixture-x", "high", "resolved",
                  protocol_fp=digest(fp1))
row = ks.db.execute(
    "SELECT protocol_fp FROM outcomes ORDER BY id DESC LIMIT 1").fetchone()
ok("outcome 落协议指纹", row is not None and row[0] == digest(fp1))

m = BaselineMatrix(suite_id="s1", runs=[
    RegressionRun(run_id="r0", suite_id="s1", run_index=0,
                  protocol_fingerprint="fp-1"),
    RegressionRun(run_id="r1", suite_id="s1", run_index=1,
                  protocol_fingerprint="fp-1"),
    RegressionRun(run_id="r2", suite_id="s1", run_index=2,
                  protocol_fingerprint="fp-1"),
], min_runs=3, stable_rate=2 / 3)
target = cluster()
v = verify_after(m, target, [cluster("drawCalls", 99)], after_protocol="fp-1")
ok("回归判定自带协议指纹（不再为空）",
   v.protocol_fingerprint == "fp-1", v.protocol_fingerprint)

ok("知识库默认路径指向 knowledge/patterns.db",
   default_path().name == "patterns.db")

# ── 工单操作：退回修改 / 单条驳回 / 意见回传 ────────────────
from upi.core.ticket import (TicketExecutor, reject_item,  # noqa: E402
                             request_changes)
store = TicketStore(tmp / "tickets")

t4 = build_ticket([cluster(rel=30)], fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks)
t4.status = "awaiting_approval"
good, msg = request_changes(t4, approver="tester", note="先补证据再改")
ok("退回修改：状态 changes_requested 且意见落库",
   good and t4.status == "changes_requested"
   and t4.change_request == "先补证据再改"
   and len(t4.change_signatures) == 1, f"{good} {msg} {t4.status}")
store.save(t4)
notes = store.recent_change_requests()
ok("退回意见可按机制签名回填下一轮",
   any(n["metric"] == "gc_alloc_per_frame" and n["note"] == "先补证据再改"
       for n in notes))
t5 = build_ticket([cluster(rel=40)], fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks, previous_notes=notes)
ok("下一轮 item 带上退回意见",
   t5.items[0].change_request == "先补证据再改"
   and "退回意见" in t5.items[0].error,
   f"{t5.items[0].change_request!r} / {t5.items[0].error!r}")

t6 = build_ticket([cluster(rel=50)], fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks)
t6.status = "approved"
i6 = t6.items[0]
good, msg = reject_item(t6, i6.item_id, approver="tester", note="这条不改")
ok("单条驳回：只改该条", good and i6.status == "rejected"
   and i6.error == "human rejected", f"{good} {msg}")

# 执行器必须跳过 rejected，不受它影响继续跑 pending 的另一条
t7 = build_ticket([cluster(rel=60), cluster("drawCalls", 70)],
                  fp_store=FakeFpStore(), plan_provider=auto_plan,
                  knowledge_store=ks)
for it in t7.items:
    it.human_approved = True
    it.status = "pending"
t7.items[0].status = "rejected"        # 已驳回的那条
t7.status = "approved"
applied = []
ex = TicketExecutor(store, apply=lambda p: applied.append(p.playbook_id),
                    compile_ok=lambda: True, observe=lambda: {"ok": True},
                    rollback=lambda: None)
ex.execute(t7)
ok("执行器跳过 rejected 条目、其余照常",
   len(applied) == 1 and t7.items[0].status == "rejected"
   and t7.items[1].status == "applied_unverified",
   f"{applied} {[i.status for i in t7.items]}")

ks.close()
print(f"\n==== 学习闭环机制测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
