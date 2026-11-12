"""I8d：候选→BatchTicket 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.candidates_v2 import CandidateV2  # noqa: E402
from upi.core.evidence_v2 import SourceSpan  # noqa: E402
from upi.core.ticket import build_candidate_ticket  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim" / "project"
plan = CandidateV2(
    id="c1", root_cause="hot_function:ChromaGame.UpdateHud",
    mechanism="hot_path", kind="function",
    sources=[SourceSpan(file="Assets/DevSim/Runtime/ChromaGame.cs",
                        start_line=962, end_line=980, symbol="ChromaGame.UpdateHud")],
    evidence_ids=["fn:1"], score=10, evidence_n=2,
)
t = build_candidate_ticket([plan], PROJECT, project_id="chroma")
ok("工单生成", t.project_id == "chroma" and len(t.items) == 1)
it = t.items[0]
ok("recipe 已选中", it.playbook_id == "PB-OP-HUD-TEXT-CACHE", it.playbook_id)
ok("auto pending", it.permission_tier == "auto" and it.status == "pending",
   (it.permission_tier, it.status))
ok("plan 可回滚", it.plan is not None and len(it.plan["diffs"]) == 5)

none = CandidateV2(id="c2", root_cause="hot_function:Unknown",
                   mechanism="hot_path", kind="function",
                   sources=[SourceSpan(file="Assets/X.cs", start_line=1,
                                       end_line=2, symbol="Unknown.Do")])
t2 = build_candidate_ticket([none], PROJECT)
ok("无 recipe 转人工", t2.items[0].status == "needs_human"
   and t2.items[0].plan is None)

print(f"\n==== 候选工单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
