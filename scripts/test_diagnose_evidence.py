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
   hs[0].code_anchors == ["Assets/DevSim/Runtime/ChromaGame.cs:10"],
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
r2 = nd2["diagnose"]({"issue": {"detector": "DET-CPU-GC-FRAMEALLOC",
                                "issue_model": issue.model_dump()}})
ok("有证据 ⇒ 诊断 proceed", r2.get("route") == "proceed", str(r2)[:200])
ok("诊断来源标记 evidence", r2.get("diagnose_source") == "evidence",
   r2.get("diagnose_source"))
hs2 = r2.get("hypotheses") or []
ok("假设锚点落在 ChromaGame", bool(hs2) and any(
    "ChromaGame.cs" in a for h in hs2 for a in h.get("code_anchors", [])))

print(f"\n==== 证据驱动诊断测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
