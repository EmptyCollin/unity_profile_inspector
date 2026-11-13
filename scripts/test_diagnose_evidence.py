"""I9a：证据驱动的诊断（离线，无 Unity、无夹具目录、无正则）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402
from upi.agents.nodes import make_nodes  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.candidates_v2 import CandidateV2  # noqa: E402
from upi.core.evidence_diagnosis import (candidates_from_latest,  # noqa: E402
                                         hypotheses_from_candidates)
from upi.core.evidence_v2 import (EvidenceBundleV2, FunctionStat,  # noqa: E402
                                  SourceSpan)
from upi.core.models import Evidence, Issue  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402
from upi.config.project_profile import (ProjectProfile,  # noqa: E402
                                        register_profile, unregister_profile)

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_diag_ev_"))
E.isolate(TMP / "runs")
PROJECT = compose(["c02"])          # HUD 缺陷工程，源码里能解析到 UpdateHudBody

# ── 适配层：CandidateV2 → Hypothesis ──────────────────────────
c = CandidateV2(id="c1", root_cause="hot_function:ChromaGame.UpdateHud",
                mechanism="hot_path", kind="function",
                sources=[SourceSpan(file="Assets/DevSim/Runtime/ChromaGame.cs",
                                    start_line=10, end_line=20,
                                    symbol="ChromaGame.UpdateHud")],
                evidence_ids=["fn:1"], score=12.0, evidence_n=3)
hs = hypotheses_from_candidates([c], TMP / "runs" / "x" / "evidence_v2.json")
ok("候选 → 假设", len(hs) == 1 and hs[0].root_cause == c.root_cause)
ok("锚点来自候选源码位置",
   any(a.endswith(":10") for a in hs[0].code_anchors)
   or any("ChromaGame.cs" in a for a in hs[0].code_anchors),
   str(hs[0].code_anchors))
ok("置信度由公式算，在 [0,1]", 0 <= hs[0].confidence <= 1, hs[0].confidence)
ok("无 fixture/profile 字样",
   "fixture" not in json.dumps(hs[0].model_dump(), ensure_ascii=False))
ok("证据引用非空", len(hs[0].attribution_factors.model_dump()) == 6)

# ── 空证据：不猜，明确 insufficient ────────────────────────────
empty_cands, _, _ = candidates_from_latest(PROJECT, limit=5)
ok("无证据时候选为空", empty_cands == [])

issue = Issue(signature={"dim": "gc", "metric": "gc_alloc_per_frame",
                         "mechanism": "per_frame_heap_alloc_in_hot_path",
                         "threshold": 1000.0, "threshold_source": "test"},
              link="cpu",
              evidence=[Evidence(tool="probe", value=1.0, unit="count")],
              magnitude=1.0, deviation_ratio=2.0,
              explains_share_of_regression=1.0, state="confirmed")
nd = make_nodes(TMP / "run1", live_llm=False, knowledge_db=TMP / "k1.db")
r = nd["diagnose"]({"issue": {"detector": "DET-CPU-GC-FRAMEALLOC",
                              "issue_model": issue.model_dump()}})
ok("空证据 ⇒ needs_human", r.get("route") == "needs_human", str(r)[:160])
ok("空证据 ⇒ 文案写明 insufficient_evidence",
   "insufficient_evidence" in str(r.get("error", "")))

# ── 有证据：诊断产出 hypotheses（锚点落工程源码）──────────────
idx = SourceIndex.build(PROJECT)
span = idx.resolve("ChromaGame.UpdateHudBody") or idx.resolve("ChromaGame.UpdateHud")
assert span is not None, "源码解析失败"
bundle = EvidenceBundleV2(
    run_id="r1", project_id="paper", frames=[1],
    functions=[FunctionStat(frame_idx=1, symbol=span.symbol or "ChromaGame.UpdateHudBody",
                            calls=1, total_ms=2.0, self_ms=1.0, source=span,
                            data_tier="A2")],
    raw_refs=["runs/r1/evidence/evidence_v2.json"])
store_dir = TMP / "runs" / "r1" / "evidence"
store_dir.mkdir(parents=True, exist_ok=True)
(store_dir / "evidence_v2.json").write_text(
    bundle.model_dump_json(indent=2), encoding="utf-8")

cands, b2, p2 = candidates_from_latest(PROJECT, limit=5)
ok("有证据 ⇒ 候选非空", len(cands) >= 1, str(len(cands)))
ok("候选来自证据包", b2 is not None and p2 is not None)

nd2 = make_nodes(TMP / "run2", live_llm=False, knowledge_db=TMP / "k2.db")
r2 = nd2["diagnose"]({"issue": {"issue_model": issue.model_dump(),
                                "source": "evidence"},
                      "project": str(PROJECT)})
ok("有证据但离线 ⇒ 拒下结论", r2.get("route") == "needs_human"
   and "未测推理" in str(r2.get("error", "")), str(r2)[:160])

# ── 真 LLM 诊断（打桩，不联网）──────────────────────────────
from upi.agents import factor_judge as FJ  # noqa: E402
from upi.agents import llm_planner as LP  # noqa: E402
from upi.core.attribution import FactorJudgment, build_hypothesis, rank  # noqa: E402
from upi.core.models import EditPlan  # noqa: E402

real_build = FJ.build_hypotheses_for_issue


def fake_build(cfg, issue, tuples, **kw):
    hs = []
    for rc, anchors in tuples:
        js = [FactorJudgment(factor=f, value=v, evidence_ref="e0")
              for f, v in (("proximity", 0.7), ("explains_share", 0.6),
                           ("mechanism", 0.8), ("competitiveness", 0.5),
                           ("prior_hit", 0.0), ("counterfactual", 0.5))]
        hs.append(build_hypothesis(rc, anchors, js, issue_evidence=issue.evidence))
    return rank(hs), {}


FJ.build_hypotheses_for_issue = fake_build
try:
    nd_live = make_nodes(TMP / "run2l", live_llm=True, knowledge_db=TMP / "k2l.db")
    r2l = nd_live["diagnose"]({"issue": {"issue_model": issue.model_dump(),
                                         "source": "evidence"},
                               "project": str(PROJECT)})
finally:
    FJ.build_hypotheses_for_issue = real_build
ok("LLM 诊断 ⇒ proceed", r2l.get("route") == "proceed", str(r2l)[:200])
ok("诊断来源标记 evidence_llm",
   r2l.get("diagnose_source") == "evidence_llm", r2l.get("diagnose_source"))
hs2 = r2l.get("hypotheses") or []
ok("假设锚点落在 ChromaGame", bool(hs2) and any(
    "ChromaGame.cs" in a for h in hs2 for a in h.get("code_anchors", [])))

# ── detect 也从证据包构造 Issue（不再跑夹具束）────────────────
prof = ProjectProfile(profile_id="p-evid", project_path=PROJECT, git_root=ROOT,
                      subjects=["SampleScene"], clean_subject="")
register_profile(prof, activate=True)
try:
    nd3 = make_nodes(TMP / "run3", live_llm=False, knowledge_db=TMP / "k3.db")
    d = nd3["detect"]({})
    ok("detect 从证据包出发", d.get("route") == "proceed", str(d)[:160])
    ok("Issue 标记 evidence 来源",
       (d.get("issue") or {}).get("source") == "evidence",
       (d.get("issue") or {}).get("source"))
    ok("Issue 带代码锚点与证据引用",
       bool((d.get("issue") or {}).get("issue_model", {}).get("evidence"))
       and bool((d.get("issue") or {}).get("anchor")))
    g3 = nd3["diagnose"]({"issue": d["issue"]})
    ok("离线诊断被拒（必须 LLM）", g3.get("route") == "needs_human"
       and "未测推理" in str(g3.get("error", "")), str(g3)[:120])
    p3 = nd3["plan"]({"issue": d["issue"],
                      "hypotheses": g3.get("hypotheses") or [],
                      "records": []})
    ok("离线规划必须被拒（必须 LLM）", p3.get("route") == "needs_human"
       and "LLM" in str(p3.get("error", "")), str(p3)[:160])

    # 同一 Issue 走 LLM 诊断 + LLM 规划（两者都打桩，不联网）
    d4 = nd_live["detect"]({"project": str(PROJECT)})
    ok("detect 证据 Issue（live 节点）", d4.get("route") == "proceed",
       str(d4)[:120])
    FJ.build_hypotheses_for_issue = fake_build
    try:
        g4 = nd_live["diagnose"]({"issue": d4["issue"],
                                  "project": str(PROJECT)})
    finally:
        FJ.build_hypotheses_for_issue = real_build
    ok("LLM 诊断证据 Issue ⇒ proceed", g4.get("route") == "proceed",
       str(g4)[:120])
    real_plan = LP.plan_issue
    LP.plan_issue = lambda cfg, issue, hyp, root, **k: (
        EditPlan(playbook_id="PB-LLM",
                 diffs=[{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
                         "find": "x", "replace": "y", "expect_count": 1}],
                 side_effect="unknown", permission_tier="semi",
                 touched_assets=["Assets/DevSim/Runtime/ChromaGame.cs"]),
        {"rounds": 1})
    try:
        p4 = nd_live["plan"]({"issue": d4["issue"],
                              "hypotheses": g4.get("hypotheses") or [],
                              "records": [], "project": str(PROJECT)})
    finally:
        LP.plan_issue = real_plan
    ok("LLM 规划 ⇒ PB-LLM", p4.get("route") == "proceed"
       and (p4.get("edit_plan") or {}).get("playbook_id") == "PB-LLM",
       str(p4)[:160])
finally:
    unregister_profile("p-evid")

print(f"\n==== 证据驱动诊断测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
