"""I10：候选→BatchTicket（方案必须来自注入的 LLM planner）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402

from upi.core.candidates_v2 import CandidateV2  # noqa: E402
from upi.core.evidence_v2 import SourceSpan  # noqa: E402
from upi.core.models import EditPlan  # noqa: E402
from upi.core.ticket import build_candidate_ticket  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = compose(["c02"])
cand = CandidateV2(
    id="c1", root_cause="hot_function:ChromaGame.UpdateHud",
    mechanism="hot_path", kind="function",
    sources=[SourceSpan(file="Assets/DevSim/Runtime/ChromaGame.cs",
                        start_line=962, end_line=980, symbol="ChromaGame.UpdateHud")],
    evidence_ids=["fn:1"], score=10, evidence_n=2,
)
fake_plan = EditPlan(
    playbook_id="PB-LLM",
    diffs=[{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
            "find": "private void UpdateHudBody()",
            "replace": "private void UpdateHudBody()  // cache",
            "expect_count": 1, "_why": "test"}],
    invariants_assert=["test"], side_effect="unknown",
    touched_assets=["Assets/DevSim/Runtime/ChromaGame.cs"],
    permission_tier="semi")

# 没有 planner：不得凭预制配方给方案
t = build_candidate_ticket([cand], PROJECT, project_id="paper")
it = t.items[0]
ok("无 planner → 无 plan / 人工", it.plan is None
   and it.status == "needs_human" and it.permission_tier == "blocked",
   (it.permission_tier, it.status, bool(it.plan)))

# 注入 LLM planner：方案照单接收，semi 需人工批准
t2 = build_candidate_ticket([cand], PROJECT, project_id="paper",
                            planner=lambda c: fake_plan)
it2 = t2.items[0]
ok("planner 方案挂到条目", it2.playbook_id == "PB-LLM"
   and it2.plan is not None and len(it2.plan["diffs"]) == 1)
ok("semi 档 → needs_human", it2.permission_tier == "semi"
   and it2.status == "needs_human")

# planner 拒绝：诚实转人工
t3 = build_candidate_ticket([cand], PROJECT, project_id="paper",
                            planner=lambda c: None)
ok("planner 拒绝 → 人工", t3.items[0].plan is None
   and t3.items[0].status == "needs_human")

print(f"\n==== 候选工单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
