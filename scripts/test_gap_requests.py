"""I13b：覆盖缺口必须转成补采请求，而不是只报盲区。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.gap_requests import requests_for_gaps  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else FAIL}] {name}" + ("" if cond else f"  -> {detail}"))


reqs = requests_for_gaps({"blind_spots": ["allocation", "loading",
                                          "texture_asset"]})
kinds = {r.kind for r in reqs}
ok("分配/加载要运行时补采", "replay_window" in kinds, kinds)
ok("纹理要资产清单", "asset_inventory" in kinds, kinds)
ok("纹理要引用图", "reference_graph" in kinds, kinds)
ok("请求去重", len(reqs) == len({(r.kind, r.expected) for r in reqs}))
ok("无缺口不请求", requests_for_gaps({"blind_spots": []}) == [])

reqs2 = requests_for_gaps({"blind_spots": ["render_material"]},
                          file="Assets/A.cs", method="A.Build")
ok("材质要代码/资产", {r.kind for r in reqs2} >= {"code_search",
                                             "asset_inventory"},
   {r.kind for r in reqs2})
ok("带上锚点", all(r.file == "Assets/A.cs" for r in reqs2
                if r.kind == "code_search"))

print(f"\n==== 缺口请求测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
