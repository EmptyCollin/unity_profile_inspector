"""I11c：证据请求协议（source 可用、resident 禁 replay、回归可补采）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402
from upi.core.evidence_request import (EvidenceRequest, fulfill,  # noqa: E402
                                       provider_kinds)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


PROJECT = compose(["c02"])
src = fulfill(EvidenceRequest(kind="source", reason="看源码",
                              file="Assets/DevSim/Runtime/ChromaGame.cs",
                              method="UpdateHudBody"), "resident", PROJECT)
ok("source 可补采", src.available and "UpdateHud" in src.text, src.reason)

bad = fulfill(EvidenceRequest(kind="source", reason="越界",
                              file="../secret.cs"), "resident", PROJECT)
ok("source 拒绝越界路径", not bad.available, bad.reason)

r_replay = fulfill(EvidenceRequest(kind="replay_window", reason="补帧",
                                   metric="gc_alloc_per_frame", window=16),
                   "resident", PROJECT, replay_capture=lambda r: {"text": "x"})
ok("resident 禁 replay 补采", not r_replay.available
   and "不支持" in r_replay.reason, r_replay.reason)

ok("resident 证据源无 replay", "replay_window" not in provider_kinds("resident"))
ok("regression 证据源含 replay", "replay_window" in provider_kinds("regression"))

g = fulfill(EvidenceRequest(kind="replay_window", reason="补帧",
                            metric="gc_alloc_per_frame", window=16),
            "regression", PROJECT,
            replay_capture=lambda r: {"text": "win 1-16 alloc 3.2KB"})
ok("regression replay 补采可用", g.available and "3.2KB" in g.text, g.reason)

none = fulfill(EvidenceRequest(kind="live_window", reason="补帧"), "resident",
               PROJECT)
ok("无采集器 → 明确 unavailable", not none.available and "未注入" in none.reason,
   none.reason)

print(f"\n==== 证据请求协议测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
