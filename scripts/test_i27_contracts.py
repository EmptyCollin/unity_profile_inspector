"""I27 契约测试：window 身份、Candidate/ToolFinding 窗口、Finding 无根因。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from upi.core.candidates_v2 import CandidateV2  # noqa: E402
from upi.core.finding import (AcceptanceUnit, Finding, RootCandidate,  # noqa: E402
                              Verdict, WindowRef)
from upi.core.findings import Anchor, ToolFinding  # noqa: E402
from upi.core.repair_contract import RepairContract  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def raises(fn):
    try:
        fn()
    except (ValidationError, ValueError):
        return True
    return False


w = WindowRef.from_id("cmd-10-open_shop", frame_start=100, frame_end=120)
ok("window_id 解析 index", w.index == 10 and w.action == "open_shop", w)
ok("window_id 保留", w.window_id == "cmd-10-open_shop", w.window_id)
ok("非法 window 拒收", raises(lambda: WindowRef.from_id("open_shop")))
ok("frame 顺序拒收", raises(lambda: WindowRef.from_id(
    "cmd-1-wait", frame_start=20, frame_end=10)))

c = CandidateV2(id="c1", root_cause="hot_function:X.Update",
                mechanism="hot_path", kind="function",
                window_id="cmd-10-open_shop")
ok("CandidateV2 保留 window_id", c.window_id == "cmd-10-open_shop", c)
ok("CandidateV2 同步 window_ids", c.window_ids == ["cmd-10-open_shop"], c.window_ids)
c2 = CandidateV2(id="c2", root_cause="x", mechanism="m", kind="function",
                 window_ids=["cmd-2-wait"])
ok("CandidateV2 window_ids 同步", c2.window_id == "cmd-2-wait", c2)

f = ToolFinding(finding_id="f1", anchors=[Anchor(file="A.cs", method="M")],
                window_id="cmd-10-open_shop", magnitude=2.0, threshold=1.0)
ok("ToolFinding 保留 window", f.window_id == "cmd-10-open_shop", f)
ok("ToolFinding 同步 window_ids", f.window_ids == ["cmd-10-open_shop"], f.window_ids)
ok("ToolFinding 无 window 仍允许", ToolFinding(
    finding_id="f2", anchors=[Anchor(file="A.cs", method="M")]).window_id == "")

finding = Finding(finding_id="F1", window=w, metric="frame_time",
                  evidence_refs=["counters.json"])
ok("Finding 不含 root_cause", "root_cause" not in finding.model_dump(),
   finding.model_dump())
ok("Finding 必须证据", raises(lambda: Finding(
    finding_id="F2", window=w, metric="frame_time")))

ok("Candidate 必须 finding", raises(lambda: RootCandidate(
    candidate_id="c", finding_ids=[], window_ids=["cmd-1-wait"])))
ok("Candidate 必须窗口或 global", raises(lambda: RootCandidate(
    candidate_id="c", finding_ids=["F1"])))
ok("Candidate global 允许", RootCandidate(
    candidate_id="c", finding_ids=["F1"], scope="global").scope == "global")

unit = AcceptanceUnit(unit_id="u1", finding_ids=["F1"],
                      target_windows=[w], merged_reason="overlap")
ok("AcceptanceUnit 目标窗口", unit.target_windows[0].window_id == "cmd-10-open_shop")
ok("RepairContract 默认窗口为空但仍可构造",
   RepairContract().window_ids == [])
ok("RepairContract 位置参数兼容",
   RepairContract("frame_time", 0.1).primary_metric == "frame_time")
ok("Verdict 默认 insufficient",
   Verdict(unit_id="u1").status == "insufficient_evidence")

print(f"\n==== I27 contracts: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
