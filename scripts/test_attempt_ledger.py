"""I12a：每次尝试都记账 + 停止原因 + 项目历史路径。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E  # noqa: E402
from upi.core.findings import (Anchor, RepairRecord, RunFindings,  # noqa: E402
                               ToolFinding, load_run, save_run, score_run)
from upi.core.project_store import knowledge_db  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_attempt_"))
E.isolate(TMP / "runs")

run = RunFindings(
    run_id="r-attempt", project_id="paper", mode="regression",
    created_at="2026-11-13T15:00:00", stop_reason="ladder=needs_recheck;loop=max_steps",
    repairs=[
        RepairRecord(finding_id="f1", plan_id="PB-LLM", compile_ok=True,
                     runnable=True, verdict="needs_recheck",
                     attempt_no=1, ticket_id="bt-1", diff_hash="h1",
                     stop_reason="needs_recheck"),
        RepairRecord(finding_id="f1", plan_id="PB-LLM", compile_ok=False,
                     runnable=False, verdict="needs_recheck",
                     attempt_no=2, ticket_id="bt-2", diff_hash="h2",
                     stop_reason="needs_recheck"),
    ],
    attempts=[
        {"attempt_no": 1, "plan_id": "PB-LLM", "ladder": "needs_recheck"},
        {"attempt_no": 2, "plan_id": "PB-LLM", "ladder": "needs_recheck"},
    ],
    findings=[ToolFinding(finding_id="f1", mechanism="hot_path", metric="cpu",
                          link="cpu",
                          anchors=[Anchor(file="Assets/A.cs", method="Update")])])
save_run(run)
back = load_run("r-attempt")
ok("两次尝试都持久化", len(back.repairs) == 2
   and [r.attempt_no for r in back.repairs] == [1, 2], back.repairs)
ok("attempts 与 stop_reason 持久化",
   len(back.attempts) == 2 and "max_steps" in back.stop_reason, back.stop_reason)
ok("每轮 ladder 都在", [r.verdict for r in back.repairs] ==
   ["needs_recheck", "needs_recheck"])

score = score_run({"expectations": [{
    "mechanism": "hot_path", "anchor": "Assets/A.cs::Update", "metric": "cpu"}],
    "created_at": "2026-11-13T14:00:00"}, back)
rows = score["dimensions"]["repair"]["rows"]
ok("评分看到两次尝试（不覆盖）", score["dimensions"]["repair"]["compiled"] == 1
   and score["dimensions"]["repair"]["attempts"] == 2
   and len(rows) == 2, score["dimensions"]["repair"])

ok("历史库按项目隔离", "projects/paper/knowledge" in
   str(knowledge_db("paper")).replace("\\", "/"))
src = (ROOT / "scripts" / "exam_live.py").read_text(encoding="utf-8")
ok("考试节点使用项目历史库", "_knowledge_db(a.project_id)" in src)
ok("考试节点接线了证据采集器", "evidence_collector=collector" in src)
ok("考试节点使用 regression 模式", 'mode="regression"' in src)

print(f"\n==== 尝试账本测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
