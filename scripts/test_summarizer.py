"""I12e：归纳总结 Agent 只提议、不批准；重复不重提。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.summarizer import SummarizerProposer  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.findings import (Anchor, AttributionInfo, RepairRecord,  # noqa: E402
                               RunFindings, ToolFinding)
from upi.core.labels import default_path  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_sum_"))
E.isolate(TMP / "runs")
run = RunFindings(
    run_id="r1", project_id="p", mode="regression", created_at="2026-11-13T15:00:00",
    findings=[ToolFinding(finding_id="f1", mechanism="hot_path", metric="cpu",
                          link="cpu",
                          anchors=[Anchor(file="Assets/A.cs", method="Update")],
                          evidence_refs=["w:1"],
                          attribution=AttributionInfo(confidence=0.8))],
    repairs=[RepairRecord(finding_id="f1", plan_id="PB", compile_ok=True,
                          verdict="needs_recheck")])

p = SummarizerProposer("p")
r1 = p.propose(run)
ok("首次提议", len(r1["created"]) == 1 and r1["pending"] == 1
   and r1["active"] == 0, r1)
r2 = p.propose(run)
ok("重复不重提", len(r2["created"]) == 0 and len(r2["skipped"]) == 1, r2)
store = p.store
store.decide(r1["created"][0], decision="approve", reviewer="user")
ok("批准后才 active", len(store.active()) == 1)
r3 = p.propose(run)
ok("已生效也不再提", len(r3["created"]) == 0, r3)
ok("标签库与知识库分离", "labels" in str(default_path("p")).replace("\\", "/"))

print(f"\n==== 归纳总结提议测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
