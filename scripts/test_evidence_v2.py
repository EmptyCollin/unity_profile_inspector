"""I8a：证据契约、源码索引、探针适配的离线测试。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.evidence_v2 import (AllocationSite, BlindSpot, CallStackFrame,  # noqa: E402
                                  CallStackSample, EvidenceBundleV2, EvidenceStore,
                                  FunctionStat, RenderObjectStat, from_probe_events)
from upi.core.source_index import SourceIndex  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


tmp = Path(tempfile.mkdtemp(prefix="upi_evidence_"))
proj = tmp / "Game"
(proj / "Assets" / "Scripts").mkdir(parents=True)
(proj / "Assets" / "Scripts" / "Foo.cs").write_text(
    "using System.Collections.Generic;\n"
    "public class Foo\n"
    "{\n"
    "    public void Update()\n"
    "    {\n"
    "        var x = new List<int>();\n"
    "    }\n"
    "\n"
    "    private int Calc(int a)\n"
    "    {\n"
    "        return a + 1;\n"
    "    }\n"
    "}\n", encoding="utf-8")

idx = SourceIndex.build(proj)
s = idx.resolve("Foo.Update")
ok("源码索引 Foo.Update", s is not None and s.file.endswith("Foo.cs"), s)
ok("源码索引行号", s is not None and s.start_line == 4 and s.end_line >= 6, s)
ok("源码索引 fallback", idx.resolve("Calc") is not None)
ok("源码索引未知返回 None", idx.resolve("NoSuch.Method") is None)

frame = CallStackFrame(symbol="Foo.Update", source=s)
bundle = EvidenceBundleV2(
    run_id="run-test", project_id="game", scene="Assets/Scenes/Main.unity",
    frames=[10, 11],
    callstacks=[CallStackSample(frame_idx=10, frames=[frame],
                                metric_context={"drawCalls": 120})],
    functions=[FunctionStat(frame_idx=10, symbol="Foo.Update", calls=8,
                            total_ms=1.2, self_ms=0.7, source=s)],
    render_objects=[RenderObjectStat(frame_idx=10, renderer_path="Canvas/Board",
                                     draw_calls=32, batches=4, triangles=1200)],
    allocations=[AllocationSite(frame_idx=10, bytes=4096, count=3,
                                stack=[frame], source=s)],
)
store = EvidenceStore(tmp / "runs")
path = store.save(bundle)
ok("证据落盘", path.exists())
loaded = store.load(path)
ok("证据回读", loaded.run_id == "run-test" and len(loaded.callstacks) == 1)
ok("对象级证据", loaded.render_objects[0].draw_calls == 32)
ok("分配证据", loaded.allocations[0].bytes == 4096)

empty = from_probe_events([], run_id="empty")
ok("空证据必须带 blindspot", len(empty.blindspots) >= 3
   and any(b.code == "callstack_unavailable" for b in empty.blindspots))

try:
    EvidenceBundleV2(run_id="bad")
    ok("空包无 blindspot 拒绝", False, "不应构造成功")
except Exception:
    ok("空包无 blindspot 拒绝", True)

try:
    CallStackSample(frame_idx=1, frames=[])
    ok("空调用栈拒绝", False, "不应构造成功")
except Exception:
    ok("空调用栈拒绝", True)

print(f"\n==== 证据 v2 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
