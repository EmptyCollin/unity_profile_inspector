"""I8b：证据图 + 候选根因的离线测试。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.candidates_v2 import generate_candidates  # noqa: E402
from upi.core.evidence_graph import EvidenceGraph  # noqa: E402
from upi.core.evidence_v2 import (AllocationSite, CallStackFrame,  # noqa: E402
                                  CallStackSample, EvidenceBundleV2,
                                  FunctionStat, RenderObjectStat)
from upi.core.fingerprint import FingerprintStore  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


tmp = Path(tempfile.mkdtemp(prefix="upi_candidates_"))
proj = tmp / "Game"
(proj / "Assets" / "Scripts").mkdir(parents=True)
(proj / "Assets" / "Scripts" / "Foo.cs").write_text(
    "public class Foo\n{\n    public void Update()\n    {\n        var a = new int[16];\n    }\n}\n",
    encoding="utf-8")
idx = SourceIndex.build(proj)
span = idx.resolve("Foo.Update")
assert span is not None

frame = CallStackFrame(symbol="Foo.Update", source=span)
bundle = EvidenceBundleV2(
    run_id="r", project_id="game", frames=[7],
    functions=[
        FunctionStat(frame_idx=7, symbol="Foo.Update", calls=10, total_ms=2.0,
                     self_ms=1.5, source=span),
        FunctionStat(frame_idx=7, symbol="EditorLoop", calls=1, total_ms=100.0),
    ],
    callstacks=[CallStackSample(frame_idx=7, frames=[frame], weight=2.0)],
    allocations=[AllocationSite(frame_idx=7, bytes=1024, count=3, stack=[frame],
                                source=span)],
    render_objects=[RenderObjectStat(frame_idx=7, renderer_path="Board",
                                     draw_calls=12)],
)
g = EvidenceGraph.from_bundle(bundle, proj)
ok("证据图有帧/函数/源码/分配", len(g.nodes) >= 5, len(g.nodes))
ok("工程函数过滤", len(g.project_functions()) == 1,
   [n.label for n in g.project_functions()])

cs = generate_candidates(bundle, g, project_root=proj)
ok("生成候选", len(cs) >= 2, len(cs))
ok("分配候选优先", cs[0].kind == "allocation", cs[0].kind)
ok("候选有源码", cs[0].sources and cs[0].sources[0].file.endswith("Foo.cs"),
   cs[0].sources)
ok("候选不来自 EditorLoop", all("EditorLoop" not in c.root_cause for c in cs))
ok("候选有 evidence id", all(c.evidence_ids for c in cs))
ok("候选 limit", len(generate_candidates(bundle, g, project_root=proj, limit=1)) == 1)
ok("单帧标 single_frame", all(c.interval_basis == "single_frame" for c in cs),
   [(c.kind, c.interval_basis) for c in cs])
ok("候选带区间", all(c.score_interval_low <= c.score <= c.score_interval_high
                 for c in cs))

fp = FingerprintStore(tmp / "fp.db")
fp.seed_builtin()
with_fp = generate_candidates(bundle, g, project_root=proj, fingerprint_store=fp)
alloc = next((c for c in with_fp if c.kind == "allocation"), None)
ok("指纹只做 prior", alloc is not None and alloc.prior > 0
   and alloc.source_kind == "evidence+fingerprint", alloc)
novel = generate_candidates(bundle, g, project_root=proj)
ok("无指纹仍是候选", any(c.prior == 0 for c in novel))

print(f"\n==== 证据图/候选测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
