"""I12e：标签入库唯一通道（提议 → 用户批/驳）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E  # noqa: E402
from upi.core.labels import LabelProposal, LabelStore  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_labels_"))
E.isolate(TMP / "runs")
store = LabelStore("paper", TMP / "labels.db")

p = LabelProposal(project_id="paper", run_id="r1",
                  mechanism="per_instance_material",
                  file="Assets/DevSim/Runtime/ChromaGame.cs",
                  method="ChromaGame.BuildBoard", frame_start=120,
                  frame_end=160, metric="drawCalls",
                  evidence_refs=["w:1"], confidence=0.8,
                  rationale="drawCalls 阶跃 + 每实例材质调用")
rec, why = store.propose(p)
ok("首次提议进入待审", rec is not None and rec["status"] == "proposed", why)
ok("待审不等于生效", len(store.active()) == 0)
ok("未批准不能审批为空", store.decide(rec["label_id"], decision="approve",
                              reviewer="")[0] is False)

dup, why2 = store.propose(p)
ok("重复不再提议", dup is None and "已存在" in why2, why2)

ok("用户批准后生效", store.decide(rec["label_id"], decision="approve",
                            reviewer="user", reason="确认")[1] == "approved")
ok("active 只有批准项", len(store.active()) == 1)

p2 = LabelProposal(project_id="paper", mechanism="sync_load",
                   file="Assets/DevSim/Runtime/ChromaGame.cs",
                   method="ChromaGame.OpenShop", frame_start=400,
                   metric="frame_time_ms", confidence=0.6)
r2, _ = store.propose(p2)
ok("第二条进入待审", r2 is not None and r2["status"] == "proposed")
store.decide(r2["label_id"], decision="reject", reviewer="user",
             reason="不是目标问题")
ok("驳回不生效", len(store.active()) == 1)
again, why3 = store.propose(p2)
ok("驳回后不自动重提", again is None and "rejected" in why3, why3)

ok("标签按项目隔离",
   len(LabelStore("other", TMP / "other.db").list()) == 0)
print(f"\n==== 标签入库测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
