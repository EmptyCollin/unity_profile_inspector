"""I1a：统一探针事件 + 事件日志 + 1Hz 哨兵（离线）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E                       # noqa: E402
from upi.core.models import A4Frame, BreachSample      # noqa: E402
from upi.core.probe_events import (EventLog, ProbeEvent, events_from_a2,  # noqa: E402
                                   from_breach_sample)
from upi.core.sentinel import check_pairs, compare     # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_i1_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def _sample() -> BreachSample:
    a4 = [A4Frame(frame_idx=100 + i, cpu_frame_time_ms=1.0,
                  gpu_frame_time_ms=2.0, complete_ts=1) for i in range(4)]
    return BreachSample(
        frame_idx=100, rel=12, metric="frame_time_ms", value=45.0,
        threshold_abs=33.333, deviation_ratio=1.35,
        ring_before=[1.0] * 10, ring_after=[1.0] * 14,
        gc_ring_before=[0.0] * 10, gc_ring_after=[0.0] * 14,
        a4_window=a4, data_tier="A2", protocol_fingerprint="fp-test",
        replay_seed=20261111,
    )


# ── breach → ProbeEvent ──
ev = from_breach_sample(_sample(), session_id="s1", raw_ref="runs/_probe/x.jsonl")
ok("breach 事件 kind/metric/rel", ev.kind == "breach" and ev.metric == "frame_time_ms"
   and ev.rel_frame == 12)
ok("breach 保留 A4 窗口", len(ev.a4_window) == 4 and ev.a4_window[0].gpu_frame_time_ms == 2.0)
ok("breach 保留 replay_seed/协议", ev.replay_seed == 20261111 and ev.protocol_fp == "fp-test")
ok("breach 保留 gc 环到 meta", ev.meta["gc_ring_after"] == [0.0] * 14)

# ── sample → ProbeEvent（每题一条）──
data = {"frame": 2888, "why": "window_done",
        "series": {"Render/DrawCalls": [1, 2, 3, 4],
                   "Memory/GcAllocInFrameBytes": [0, 10, 20]},
        "skipped": ["Render/Indices"]}
sevs = events_from_a2(data, session_id="s1", raw_ref="runs/_probe/data.json")
ok("A2 series 拆成两条 sample", len(sevs) == 2)
ok("sample 指标名保留", {e.metric for e in sevs} ==
   {"Render/DrawCalls", "Memory/GcAllocInFrameBytes"})
ok("sample 统计 median/p95", sevs[0].stats["median"] == 2.5 and sevs[0].stats["p95"] == 4.0)
ok("sample 保留 why/skipped", sevs[0].meta["why"] == "window_done"
   and sevs[0].meta["skipped"] == ["Render/Indices"])
ok("metrics 摘要兜底", len(events_from_a2({"metrics": {"drawCalls": 47.0}})) == 1)

# ── 事件日志：追加/读取/坏行计数/tail/stats ──
log = EventLog()
for e in [ev, *sevs]:
    log.append(e)
evs, bad = log.read_all()
ok("日志读回 3 条", len(evs) == 3 and bad == 0)
ok("tail 取最后 1 条", len(log.tail(1)) == 1 and log.tail(1)[0].kind == "sample")
st = log.stats()
ok("stats 按 kind 计数", st["by_kind"].get("breach") == 1 and st["by_kind"].get("sample") == 2)
ok("stats 有 last_ts", bool(st["last_ts"]))

with log.path.open("a", encoding="utf-8") as f:
    f.write("{bad json}\n")
_evs2, bad2 = log.read_all()
ok("坏行跳过并计数（不静默）", bad2 == 1 and len(_evs2) == 3)

# ── 旧两源兼容桥（I1a）：统一日志不存在时读 pb_breach/data.json ──
pd = E.RUNS / "_probe"
pd.mkdir(parents=True, exist_ok=True)
(pd / "pb_breach.jsonl").write_text(_sample().model_dump_json() + "\n", encoding="utf-8")
(pd / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
legacy, lbad = EventLog(path=TMP / "not_exists.jsonl").read_all(compat=True)
ok("旧两源兼容读回 3 条", len(legacy) == 3 and lbad == 0)
ok("兼容读回含 breach+sample", {e.kind for e in legacy} == {"breach", "sample"})

# ── 契约负例 ──
try:
    ProbeEvent(kind="sample")
    ok("sample 空载荷被拒", False)
except Exception:
    ok("sample 空载荷被拒", True)
try:
    ProbeEvent(kind="breach", value=1.0)
    ok("breach 缺 metric 被拒", False)
except Exception:
    ok("breach 缺 metric 被拒", True)

# ── 1Hz 哨兵 ──
ok("哨兵比值 1.0 通过", compare(100, 100).ok)
ok("哨兵比值 0.01 拒绝", not compare(1, 100).ok)
ok("哨兵 0/0 不判通过（无信息量）",
   (not compare(0, 0).ok) and compare(0, 0).reason == "zero_to_zero_no_info")
ok("哨兵 poll=0 probe>0 拒绝", not compare(3, 0).ok)
r = check_pairs({"draw": (100, 100), "gc": (0, 0)})
ok("批量哨兵逐项判", r["draw"].ok and not r["gc"].ok)

print()
if FAIL:
    print(f"==== 探针事件/哨兵测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print(f"==== 探针事件/哨兵测试: {len(PASS)} 通过, 0 失败")
