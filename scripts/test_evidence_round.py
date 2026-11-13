"""I11c：诊断定不了因 → 向检查补采一次 → 再诊断（双向链路的节点级）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402
from upi.agents import factor_judge as FJ  # noqa: E402
from upi.agents.nodes import make_nodes  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.attribution import FactorJudgment, build_hypothesis, rank  # noqa: E402
from upi.core.evidence_v2 import (EvidenceBundleV2, FunctionStat,  # noqa: E402
                                  SourceSpan)
from upi.core.models import Evidence, Issue  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_evround_"))
E.isolate(TMP / "runs")
PROJECT = compose(["c02"])
idx = SourceIndex.build(PROJECT)
span = idx.resolve("ChromaGame.UpdateHudBody") or idx.resolve("ChromaGame.UpdateHud")
assert span is not None

bundle = EvidenceBundleV2(
    run_id="r1", project_id="p", frames=[1],
    functions=[FunctionStat(frame_idx=1, symbol=span.symbol or "ChromaGame.UpdateHud",
                            calls=1, total_ms=3.0, self_ms=2.0, source=span,
                            data_tier="A2")],
    raw_refs=["runs/r1/evidence/evidence_v2.json"])
d = TMP / "runs" / "r1" / "evidence"
d.mkdir(parents=True, exist_ok=True)
(d / "evidence_v2.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

issue = Issue(signature={"dim": "cpu", "metric": "cpuMainThreadFrameTimeMs",
                         "mechanism": "hot_path", "threshold": 16.0,
                         "threshold_source": "test"},
              link="cpu",
              evidence=[Evidence(tool="probe", value=33.0, unit="ms")],
              magnitude=17.0, deviation_ratio=2.0,
              explains_share_of_regression=1.0, state="confirmed")

calls, collected = [], []


def _hs(quality: float):
    js = [FactorJudgment(factor=f, value=quality, evidence_ref="e0")
          for f in ("proximity", "explains_share", "mechanism",
                    "competitiveness", "prior_hit", "counterfactual")]
    return [build_hypothesis("hot_function:ChromaGame.UpdateHud",
                             ["Assets/DevSim/Runtime/ChromaGame.cs::ChromaGame.UpdateHud"],
                             js, issue=issue)]


def fake_build(cfg, issue, tuples, **kw):
    calls.append(1)
    q = 0.15 if len(calls) == 1 else 0.95
    return rank(_hs(q)), {}


real = FJ.build_hypotheses_for_issue
FJ.build_hypotheses_for_issue = fake_build
try:
    nd = make_nodes(TMP / "runA", live_llm=True, mode="regression",
                    knowledge_db=TMP / "k.db",
                    evidence_collector=lambda req: (collected.append(req) or
                                                    {"available": True,
                                                     "text": "帧 1-32 分配 3.2KB"}))
    r = nd["diagnose"]({"issue": {"issue_model": issue.model_dump()},
                        "project": str(PROJECT)})
finally:
    FJ.build_hypotheses_for_issue = real

ok("补采后诊断 proceed", r.get("route") == "proceed", str(r)[:160])
ok("补采了一次", len(collected) == 1, len(collected))
ok("补采使用 replay 窗口（regression）",
   collected and collected[0].kind == "replay_window", collected)
ok("诊断调用了两次 LLM", len(calls) == 2, len(calls))
ok("补采留痕文件", (TMP / "runA" / "evidence_round_1.json").exists())

# resident 只允许 1 轮；两轮都低 → 最终 needs_human，且只补采一次
calls.clear()
collected.clear()
FJ.build_hypotheses_for_issue = lambda cfg, issue, tuples, **kw: (
    calls.append(1) or (rank(_hs(0.15)), {}))
try:
    nd2 = make_nodes(TMP / "runB", live_llm=True, mode="resident",
                     knowledge_db=TMP / "k2.db",
                     evidence_collector=lambda req: (collected.append(req) or
                                                     {"available": True,
                                                      "text": "live 窗口"}))
    r2 = nd2["diagnose"]({"issue": {"issue_model": issue.model_dump()},
                          "project": str(PROJECT)})
finally:
    FJ.build_hypotheses_for_issue = real
ok("resident 只补采一次", len(collected) == 1, len(collected))
ok("仍定不了因 → needs_human", r2.get("route") == "needs_human", str(r2)[:120])

print(f"\n==== 证据补采回路测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
