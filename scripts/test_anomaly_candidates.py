"""I12c：事件 → 异常窗口 → 候选（无 action 依赖）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.anomaly_candidates import (candidates_from_anomalies,  # noqa: E402
                                         windows_from_bundle,
                                         windows_from_events)
from upi.core.evidence_v2 import (AllocationSite, EvidenceBundleV2,  # noqa: E402
                                  FunctionStat, RenderObjectStat, SourceSpan)
from upi.core.probe_events import ProbeEvent  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


series = [10.0] * 60
series[40] = 80.0
ev = ProbeEvent(kind="sample", metric="frame_time_ms", frame_idx=59,
                series=series, data_tier="A2")
wins = windows_from_events([ev])
ok("采到异常窗口", any(w["metric"] == "frame_time_ms" for w in wins), wins)
ok("窗口覆盖尖峰帧", any(w["start_frame"] <= 40 <= w["end_frame"]
                    for w in wins), wins)

bundle = EvidenceBundleV2(
    run_id="r", project_id="p", frames=[40],
    functions=[FunctionStat(frame_idx=40, symbol="ChromaGame.Update",
                            calls=1, total_ms=9.0, self_ms=8.0,
                            source=SourceSpan(file="Assets/A.cs", start_line=10,
                                              end_line=20, symbol="ChromaGame.Update"),
                            data_tier="A2")],
    anomalies=wins)
cands = candidates_from_anomalies(bundle, ROOT, limit=5)
ok("异常生成候选", bool(cands) and cands[0].kind == "anomaly", cands)
ok("候选带源码锚点", bool(cands) and cands[0].sources
   and cands[0].sources[0].file == "Assets/A.cs", cands[0].sources if cands else None)
ok("候选根因可追溯", bool(cands) and "@" in cands[0].root_cause, cands)
ok("候选不依赖 action", "action" not in (cands[0].root_cause if cands else ""))

# 异常窗口没有函数时，不硬造候选
b2 = EvidenceBundleV2(run_id="r2", project_id="p", frames=[40], anomalies=wins)
ok("无函数不硬造", candidates_from_anomalies(b2, ROOT) == [])

# 无 breach 时，用证据自身触发窗口
b3 = EvidenceBundleV2(
    run_id="r3", project_id="p", frames=[10],
    functions=[FunctionStat(frame_idx=10, symbol="ChromaGame.Update",
                            calls=1, total_ms=12.0,
                            source=SourceSpan(file="Assets/A.cs", start_line=1,
                                              end_line=2, symbol="ChromaGame.Update"))],
    allocations=[AllocationSite(frame_idx=10, bytes=2 * 1024 * 1024, count=1)],
    render_objects=[RenderObjectStat(frame_idx=10, renderer_path="Board",
                                     draw_calls=200)])
w3 = windows_from_bundle(b3)
ok("证据触发窗口", {w["metric"] for w in w3} >= {"gc_alloc_per_frame",
                                             "frame_time_ms", "drawCalls"}, w3)
ok("窗口覆盖证据帧", all(w["start_frame"] <= 10 <= w["end_frame"] for w in w3))

print(f"\n==== 异常候选测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
