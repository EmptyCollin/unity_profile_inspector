"""I25-ext E3：load_breach_samples 注入 window。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.breach_detector import load_breach_samples  # noqa: E402
from upi.core.models import A4Frame, BreachSample  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


a4 = [A4Frame(frame_idx=100 + i, cpu_frame_time_ms=1.0,
              gpu_frame_time_ms=2.0, complete_ts=1) for i in range(4)]
s = BreachSample(
    frame_idx=100, rel=12, metric="frame_time_ms", value=45.0,
    threshold_abs=33.333, deviation_ratio=1.35,
    ring_before=[1.0] * 10, ring_after=[1.0] * 14,
    gc_ring_before=[0.0] * 10, gc_ring_after=[0.0] * 14,
    a4_window=a4, data_tier="A2", protocol_fingerprint="fp",
    replay_seed=20261111)
p = Path(tempfile.mkdtemp()) / "x.jsonl"
p.write_text(json.dumps(s.model_dump(mode="json")) + "\n", encoding="utf-8")

samples, stats = load_breach_samples(p, window="open_shop")
ok("sample 数", len(samples) == 1 and not stats.get("contract_reject"), stats)
ok("window 注入", samples[0].window == "open_shop", samples[0].window)

s2, _ = load_breach_samples(p)
ok("未注入保持空", s2[0].window == "", s2[0].window)

print(f"\n==== I25-ext window 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
