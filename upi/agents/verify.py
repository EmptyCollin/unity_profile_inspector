"""验证窗（M3，AGENTS §1.5 / G1 / G2 / G7 / G17 / DESIGN §4）。

三条口径在本模块的落点：
- **同仪器状态才可比（G7 引申，本轮实测要求）**：验证窗的"改前束"不复用
  历史标定文件——标定之后常驻件（探针/驱动器/A2 注入）变了，仪器状态不同。
  before/after 都必须当场重采，两束同协议同窗口同常驻态，差值才只归因于修复。
- G1：目标指标改善 < k·σ → not_significant（视为未解决）；σ 用**改前束**的
  窗口间 σ（修复不应改变噪声定义）。G17 退化时改走 min_magnitude 绝对门限。
- G2：全量守护指标（bundle 内全部指标，不只当初异常项），劣化 > 1% 即
  regressed。方向按指标语义判（higher_worse 表驱动，表外指标按 higher_worse 保守处理）。
- P4：run_pair 矛盾表随验证窗跑（T7 接入点），conflicts 非空即进 Review。
- P2/G3：语义断言与感知门禁基建在 M7，本模块**如实缺席**——Perception 字段
  全 None + report 里 perception_gate_pending=true，不得让 status=resolved 被
  读成"感知也过了"。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.m1_detect import apply_calibration, latest_calibration, stats_from_windows  # noqa: E402
from upi.core.conflicts import run_pair  # noqa: E402
from upi.core.detector import DEFAULT_REGISTRY  # noqa: E402
from upi.core.models import Conflict, GuardMetric, Perception, Verdict  # noqa: E402

GUARD_TOLERANCE = 0.01          # §1.5 默认 1%

# 指标方向表（bundle 键名）。表外一律按 higher_worse 保守判。
HIGHER_IS_WORSE = {
    "drawCalls", "setPassCalls", "batches", "triangles", "vertices",
    "totalAllocatedBytes", "totalReservedBytes", "monoUsedBytes", "monoHeapBytes",
    "cpuFrameTimeMs", "gpuFrameTimeMs", "cpuMainThreadFrameTimeMs",
    "gc_alloc_per_frame", "animator_process_animations_ns",
    "animator_process_graph_ns", "total_used_per_frame", "frame_time_ms",
}
# "越高越好"的少数派——available 只表示数据可用性，不入守护
NOT_GUARDED = {"available", "scale_factor"}


def resample(fixture: str, tag: str, windows: int = 4, timeout_s: int = 1500) -> dict:
    """当场重采一个束（full 会话 = A+A2，与标定同协议）。

    前置检查（实测教训）：常驻件在不在就在那儿，不在别硬采——
    A2 注入失败 m1_multiwin 自身会返 error，这里只透传并拒绝空束。"""
    out_dir = ROOT / "runs" / tag
    cmd = [sys.executable, str(ROOT / "scripts" / "m1_multiwin.py"),
           "--session", "full", "--fixtures", fixture,
           "--windows", str(windows), "--caldir", str(out_dir)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       encoding="utf-8", errors="replace")
    fp = out_dir / "multiwin_full.json"
    if not fp.exists():
        raise RuntimeError(f"重采失败（{tag}）：无输出文件。tail:\n{p.stdout[-800:]}{p.stderr[-300:]}")
    d = json.loads(fp.read_text(encoding="utf-8"))
    d["_path"] = str(fp)
    fx = d.get("fixtures", {}).get(fixture, {})
    if "windows" not in fx:
        raise RuntimeError(f"重采失败（{tag}）：bundle 无 {fixture} 窗口数据 "
                           f"（error={fx.get('error', '?')}）")
    return d


def bundle_stats(doc: dict, fixture: str) -> dict:
    fx = doc["fixtures"][fixture]
    return stats_from_windows(fx)


def verdict_from_bundles(det_id: str, fixture: str,
                        before_doc: dict, after_doc: dict,
                        compile_ok: bool) -> tuple[Verdict, dict]:
    """核心判定。返回 (Verdict, 详情 dict——详情供 trace/report 落盘)。"""
    det = next(d for d in DEFAULT_REGISTRY if d.id == det_id)
    cal = latest_calibration()
    if cal:
        apply_calibration([det], cal)        # 阈值只认标定值（§8.1）
    bstats = bundle_stats(before_doc, fixture)
    astats = bundle_stats(after_doc, fixture)

    m = det.metric
    if m not in bstats or m not in astats:
        raise RuntimeError(f"目标指标 {m} 在某一束缺失——insufficient_evidence，"
                           "不得强行给结论（I2）")

    before, after = bstats[m], astats[m]
    improvement = before["median"] - after["median"]     # higher_worse：正=改善
    sigma = before["noise_band_mad"]                      # σ 用改前束（噪声定义不随修复变）
    degenerate = before["degenerate_distribution"] or after["degenerate_distribution"]
    k = det.thresholds.k_sigma
    if degenerate:
        significant = improvement > det.thresholds.min_magnitude
        sig_basis = f"degenerate ⇒ 绝对门限 min_magnitude={det.thresholds.min_magnitude}"
    else:
        significant = improvement > k * sigma
        sig_basis = f"k·σ = {k}×{sigma:.3g}"

    guards, worst = [], 0.0
    for key, bv in sorted(bstats.items()):
        if key in NOT_GUARDED or key == m:
            continue
        av = astats.get(key)
        if av is None:
            continue
        delta = av["median"] - bv["median"]
        # HIGHER_IS_WORSE 是集合：表内与表外一律按"越高越差"保守判；
        # 未来若有"越低越差"的指标，新增 LOWER_IS_WORSE 集合显式登记，
        # 不给隐式默认留活口（上一版写成 .get(key, True) 直接崩——
        # set 没有 get；彩排实测撞出）
        worse = delta > 0
        rel = abs(delta) / max(abs(bv["median"]), 1e-9)
        reg = worse and rel > GUARD_TOLERANCE
        guards.append(GuardMetric(metric=key, before=round(bv["median"], 3),
                                  after=round(av["median"], 3),
                                  delta=round(delta, 3), regressed=reg))
        if reg:
            worst = max(worst, rel)

    conflicts = run_pair({k2: v["median"] for k2, v in bstats.items()},
                         {k2: v["median"] for k2, v in astats.items()})

    if not compile_ok:
        status = "regressed"
    elif conflicts:
        status = "regressed"          # §1.5：矛盾违反 = 视同守护劣化
    elif worst > 0:
        status = "regressed"
    elif significant:
        status = "resolved"
    else:
        status = "not_significant"

    # 感知门禁：M7 前如实缺席（不假实现）
    perception = Perception(render_diff_score=None, anim_event_diff=None,
                            replay_divergence=None, passed=False)

    realized = improvement / det.thresholds.min_magnitude if det.thresholds.min_magnitude else 0.0
    v = Verdict(
        target={"metric": m, "before": before["median"], "after": after["median"],
                "delta": round(improvement, 4), "sigma": sigma,
                "significant": significant, "significance_basis": sig_basis,
                "degenerate": degenerate},
        guards=guards, perception=perception, conflicts=conflicts,
        compile_ok=compile_ok,
        realized_over_expected=round(realized, 3),
        status=status)     # type: ignore[arg-type]
    detail = {"detector": det_id, "fixture": before,
              "before_path": before_doc.get("_path"), "after_path": after_doc.get("_path"),
              "guard_worst_rel": round(worst, 5),
              "perception_gate_pending": True,   # M7 落地前不得被读成"感知已过"
              "conflicts": [c.model_dump() for c in conflicts]}
    return v, detail
