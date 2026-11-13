"""I13b：窗口证据 = 运行时 + 静态结构 + 覆盖缺口。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402
from upi.core.evidence_v2 import (AllocationSite, EvidenceBundleV2,  # noqa: E402
                                  FunctionStat, RenderObjectStat, SourceSpan)
from upi.core.static_index import StaticIndex  # noqa: E402
from upi.core.window_evidence import coverage, explain_window  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else FAIL}] {name}" + ("" if cond else f"  -> {detail}"))


PROJECT = compose(["c02"])
idx = StaticIndex(PROJECT)
src = SourceSpan(file="Assets/DevSim/Runtime/ChromaGame.cs", start_line=818,
                 end_line=821, symbol="ChromaGame.Update")
bundle = EvidenceBundleV2(
    run_id="r", project_id="p", frames=[40, 41],
    functions=[FunctionStat(frame_idx=40, symbol="ChromaGame.Update",
                            calls=1, total_ms=5.0, source=src)],
    allocations=[AllocationSite(frame_idx=40, bytes=320, count=2, stack=[])],
    render_objects=[RenderObjectStat(frame_idx=40, renderer_path="Board",
                                     draw_calls=10, material_ids=["m1"])],
)
cov = coverage(bundle)
ok("分配覆盖为真", cov["allocation"] is True, cov)
ok("渲染覆盖为真", cov["render_material"] is True, cov)
ok("加载/纹理缺口显式", "loading" in cov["blind_spots"]
   and "texture_asset" in cov["blind_spots"], cov)

ex = explain_window(bundle, idx, 40, 41)
ok("窗口有函数/分配/渲染", ex["runtime"]["functions"] == ["ChromaGame.Update"]
   and ex["runtime"]["allocations"] == 1
   and ex["runtime"]["render_objects"] == 1, ex["runtime"])
ok("静态证据带边界", ex["evidence_class"] == "runtime+static"
   and "limits" in ex, ex)
ok("静态提示可空但不报错", isinstance(ex["static"], dict))

empty = EvidenceBundleV2(run_id="r2", project_id="p", frames=[1])
cov2 = coverage(empty)
ok("空包缺口全列", "allocation" in cov2["blind_spots"]
   and "render_material" in cov2["blind_spots"], cov2)

empty.counters = {"GC Allocated In Frame": {"series": [1]},
                  "Draw Calls Count": {"series": [2]}}
cov3 = coverage(empty)
ok("通用计数补齐分配/渲染", cov3["allocation"] and cov3["render_material"],
   cov3)

print(f"\n==== 窗口证据测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
