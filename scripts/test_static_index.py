"""I12b：静态 S 层（代码/资产/配置）与证据能力边界。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402
from upi.core.evidence_request import EvidenceRequest, fulfill  # noqa: E402
from upi.core.static_index import StaticIndex  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


PROJECT = compose(["c02"])
idx = StaticIndex(PROJECT)
ok("快照哈希非空", bool(idx.snapshot_hash), idx.snapshot_hash)
ok("文件清单覆盖 Assets", any(f["file"].startswith("Assets/")
                              for f in idx.files), len(idx.files))
ok("代码索引有方法", bool(idx.methods), len(idx.methods))
ok("源切片可读", "UpdateHud" in idx.source_slice(
    "Assets/DevSim/Runtime/ChromaGame.cs", "UpdateHudBody"))
ok("符号查找", any(r["method"] == "UpdateHud"
                   for r in idx.find_symbol("UpdateHud")))
ok("调用点可查", isinstance(idx.callers("UpdateBody"), list))
ok("资产清单有纹理/资源", isinstance(idx.asset_inventory(), list))
ok("配置摘要可读", isinstance(idx.settings_summary(), dict))
ok("同一工程快照稳定", StaticIndex(PROJECT).snapshot_hash == idx.snapshot_hash)

# 证据请求：静态类必须带 evidence_class / 能力边界
r = fulfill(EvidenceRequest(kind="source", reason="看源码",
                            file="Assets/DevSim/Runtime/ChromaGame.cs",
                            method="UpdateHudBody"), "resident", PROJECT)
ok("source 请求可用", r.available and r.evidence_class == "static", r.reason)
ok("静态证据带 can/cannot", "source_location" in r.can_support
   and "runtime_frequency" in r.cannot_support, r.cannot_support)

r2 = fulfill(EvidenceRequest(kind="code_search", reason="查符号",
                             expected="UpdateHud"), "resident", PROJECT)
ok("code_search 可用", r2.available and "ChromaGame.cs" in r2.text, r2.text[:120])

r3 = fulfill(EvidenceRequest(kind="asset_inventory", reason="资产",
                             expected="", window=0), "resident", PROJECT)
ok("asset_inventory 可用", r3.available and r3.evidence_class == "static",
   r3.reason)

r4 = fulfill(EvidenceRequest(kind="settings", reason="配置"), "resident", PROJECT)
ok("settings 可用", r4.available and "snapshot_hash" in r4.text)

r5 = fulfill(EvidenceRequest(kind="ast_callgraph", reason="调用图",
                             method="UpdateBody"), "resident", PROJECT)
ok("ast_callgraph 返回（可空）", r5.kind == "ast_callgraph"
   and r5.evidence_class == "static", r5.reason)

print(f"\n==== 静态索引测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
