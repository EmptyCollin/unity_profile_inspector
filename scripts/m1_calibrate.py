"""M1 标定：从最新基线的对照机制统计生成检测阈值（DESIGN §8.1 的唯一合法来源）。

⚠️ 本入口已被 `m1_multiwin.py --merge`（do_merge）取代：检测端
  （scripts/m1_detect.py）明确禁止回退本脚本读的旧单窗口基线，
  `mw is None` 时直接报"检测与标定必须来自同一多窗口数据源"。
  实测本脚本作为独立入口仍可运行，所以对照机制名照样按档案取，不留写死字面。

规则：
  min_magnitude = max(指标地板, 6 × clean_noise_band_mad)   # 6σ≈百万分之一误报率（近正态下）
  band=0（clean 分布退化）时地板兜底，并标 degenerate ⇒ 检测端走纯绝对门限（G17）
  deviation_ratio = 1 + 3×IQR/median（clean 相对波动上界的最小超越）
输出 baselines/thresholds_<hash>.json，由 detector.apply_calibration 注入使用。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINES = ROOT / "baselines"

from upi.config.project_profile import active_profile  # noqa: E402

# 指标地板：低于此绝对量的差异即便统计显著也不值得报（业务显著性）
FLOORS = {
    "gc_alloc_per_frame": 20_000,        # B/帧：20KB/s 级分配差异在移动端仍可观
    "animator_process_animations_ns": 50_000,   # 0.05ms/帧：动画管线开销地板
    "animator_process_graph_ns": 50_000,
    "drawCalls": 10,
    "setPassCalls": 8,
    "cpuFrameTimeMs": 0.5,
    "cpuMainThreadFrameTimeMs": 0.3,
    "totalAllocatedBytes": 2_000_000,
    "monoUsedBytes": 2_000_000,
    "batches": 5,
}


def calibrate_one(name: str, st: dict) -> dict:
    band = float(st.get("noise_band_mad", 0.0))
    med = float(st.get("median", 0.0))
    deg = bool(st.get("degenerate_distribution", False))
    floor = FLOORS.get(name, 0.0)
    min_mag = max(floor, 6 * band)
    return {
        "metric": name,
        "clean_median": med,
        "clean_band": band,
        "min_magnitude": round(min_mag, 4),
        "deviation_ratio": round(1.0 + 3 * (band / med if med > 0 else 1.0), 4),
        "k_sigma": 3.0,
        "degenerate": deg,
        "basis": "clean 6×MAD + 指标地板（§8.1）",
    }


def main() -> int:
    bps = sorted(BASELINES.glob("baseline_*.json"), key=lambda p: p.stat().st_mtime)
    if not bps:
        print("无基线文件")
        return 1
    bp = bps[-1]
    doc = json.loads(bp.read_text(encoding="utf-8"))
    clean = doc["fixtures"].get(active_profile().clean_subject)
    if not clean or "error" in clean:
        print(f"基线缺对照机制 {active_profile().clean_subject} 变体")
        return 1

    stats = {**clean.get("A_stats", {})}
    a2 = clean.get("A2_stats", {})
    for k2 in ("gc_alloc_per_frame", "animator_process_animations_ns", "animator_process_graph_ns"):
        if a2.get(k2):
            stats[k2] = a2[k2]

    out = {"source_baseline": bp.name,
           "protocol": clean.get("protocol", {}),
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "thresholds": {k: calibrate_one(k, v) for k, v in stats.items()}}
    blob = json.dumps(out, sort_keys=True).encode()
    h = hashlib.sha256(blob).hexdigest()[:12]
    fp = BASELINES / f"thresholds_{h}.json"
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"标定完成 ← {bp.name}")
    for k, t in out["thresholds"].items():
        print(f"  {k:26s} clean_med={t['clean_median']:>14.2f} band={t['clean_band']:>12.4f} "
              f"min_mag={t['min_magnitude']:>12.2f} dev_ratio={t['deviation_ratio']} 退化={t['degenerate']}")
    print("写入:", fp.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
