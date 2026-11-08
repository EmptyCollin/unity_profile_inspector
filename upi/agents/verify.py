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

# 记忆体类守护口径（a4_altertest 交替块实测后升级，见 upi/core/memory_guard.py）：
# bundle 窗口值量的是"此刻未回收多少"，回收时点一变就失真（实测 7.8MB 假象
# 里净代价只有 1.9MB）。⇒ 这类指标两侧各自采 fresh（Play→采一段→强制GC→再采一段），
# 只用 post_gc 对比；窗口值仅留档。数据缺失 = 未评估（盲区），不退回污染口径。
from upi.core.memory_guard import (FRESH_POST_GC_METRICS, is_regressed,  # noqa: E402
                                   memory_guard_entry)


def instrument_state() -> dict:
    """常驻件存活指纹（彩排第一轮实测教训：before 束测到 uncapped 1.87ms、
    after 束测到 capped 16.6ms——同一个"协议"下仪器状态根本不同，差值
    混入常驻件开关与帧率封顶变化，按 G7/§3.6 不可比，整轮作废重采）。

    三件全查：越界探针心跳、回放驱动器心跳、A2 采样器（无心跳文件，
    用 probe 侧 status 回执推断注入链在场与否不成立——A2 由 m1_multiwin
    每束自注入，其状态=注入结果，记录在各束数据里；这里查两枚心跳 +
    实时帧率封顶形态）。形态判据：连取 3 次 cpuFrameTimeMs，全部 ≈16.6±2
    ⇒ capped60；存在 <10 ⇒ uncapped。"""
    st = {}
    now = time.time()
    for name, fn in (("breachprobe_hb", "pb_hb.txt"), ("replaydriver_hb", "rp_hb.txt")):
        p = ROOT / "runs" / "_probe" / fn
        st[name] = round(now - p.stat().st_mtime, 1) if p.exists() else None
    from upi.tooling.unity_cli import run_unity
    vals = []
    for _ in range(3):
        r = run_unity(["command", "diagnostics get_performance",
                       "--project-path", str(ROOT / "fixtures" / "upi-fixture")],
                      timeout_s=60)
        ft = ((r.data or {}).get("result") or {}).get("frameTiming") or {}
        if ft.get("cpuFrameTimeMs") is not None:
            vals.append(float(ft["cpuFrameTimeMs"]))
        time.sleep(0.7)
    capped = bool(vals) and all(abs(v - 16.667) < 2.5 for v in vals)
    st["fps_mode"] = "capped60" if capped else ("uncapped" if vals else "unknown")
    st["samples_ms"] = [round(v, 3) for v in vals]
    return st


def assert_comparable(s_before: dict, s_after: dict) -> None:
    """两束仪器状态一致性硬闸（代码层防呆，不依赖人记得检查）。"""
    diffs = []
    if s_before["fps_mode"] != s_after["fps_mode"]:
        diffs.append(f"fps_mode {s_before['fps_mode']} vs {s_after['fps_mode']}")
    for k in ("breachprobe_hb", "replaydriver_hb"):
        a, b = s_before[k], s_after[k]
        if (a is None) != (b is None) or (a is not None and b is not None
                                          and max(a, b) > 5 > min(a, b)):
            diffs.append(f"{k} 存活不一致 {a} vs {b}")
    if diffs:
        raise RuntimeError("两束仪器状态不一致，禁止对比（G7 引申）：" + "; ".join(diffs))


def rp_ctl(line: str, wait_s: float = 6.0) -> str:
    """向常驻回放驱动器发文件命令并等回执（其控制轮询 10Hz）。
    驱动器心跳死时命令文件只是滞留，无害——返回值记 TIMEOUT 即可。"""
    pdir = ROOT / "runs" / "_probe"
    pdir.mkdir(exist_ok=True)
    rp_res, rp_cmd = pdir / "rp_res.txt", pdir / "rp_cmd.txt"
    old = rp_res.read_text(encoding="utf-8") if rp_res.exists() else None
    tmp = pdir / "rp_cmd.tmp"
    tmp.write_text(line, encoding="utf-8")
    tmp.replace(rp_cmd)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(0.4)
        if rp_res.exists() and rp_res.read_text(encoding="utf-8") != old:
            return rp_res.read_text(encoding="utf-8").strip()[:120]
    return "TIMEOUT"


def gc_floor_measure(fixture: str, polls: int = 10, timeout_s: int = 420) -> dict:
    """fresh 采集（记忆体守护的正确口径，见 upi/core/memory_guard.py）。

    在当前夹具态进 Play：采 N 次 memory 读数 → 强制 GC.Collect×2 →
    再采 N 次，返回 {metric: {"pre_gc": 中位, "post_gc": 中位}}。
    post_gc = "净常驻"的可观测代理——不受回收时点污染，两侧各自采集后
    对比（改前态一次、改后态一次），这才是记忆体守护该用的数。
    旧口径（只采改后态、拿 post_gc 对改前窗口值判"观测性积累"豁免）已被
    a4_altertest 实测否证：缺陷态自身也会积累未回收垃圾，单侧对比
    分不清"修复净代价"与"两侧都在涨"。
    调用方须保证夹具处于待验状态；本函数只管测量不动代码。"""
    import statistics as stx
    from upi.tooling.unity_cli import run_unity
    proj = str(ROOT / "fixtures" / "upi-fixture")
    t0 = time.time()
    run_unity(["command", "editor.playmode stop", "--project-path", proj], timeout_s=60)
    time.sleep(3)
    r = run_unity(["command", "scene open", "--project-path", proj,
                   "--path", f"Assets/UpiFixtures/Scenes/Scene_{fixture}.unity"],
                  timeout_s=200)
    if not r.success:
        raise RuntimeError(f"地板复核开场景失败: {r.errors[:1]}")
    time.sleep(5)
    run_unity(["command", "editor.playmode start", "--project-path", proj], timeout_s=90)
    time.sleep(8)   # 进 Play 稳定（F-LOAD-01 的首帧巨量加载走完）

    keys = tuple(sorted(FRESH_POST_GC_METRICS))

    def poll_median() -> dict:
        vals: dict[str, list[float]] = {k: [] for k in keys}
        for _ in range(polls):
            if time.time() - t0 > timeout_s - 30:
                break
            rr = run_unity(["command", "diagnostics get_performance",
                            "--project-path", proj], timeout_s=60)
            mem = ((rr.data or {}).get("result") or {}).get("memory") or {}
            for k in keys:
                if isinstance(mem.get(k), (int, float)):
                    vals[k].append(float(mem[k]))
            time.sleep(0.6)
        return {k: (stx.median(v) if v else None) for k, v in vals.items()}

    pre = poll_median()
    gc_r = run_unity(["command", "script eval", "--project-path", proj, "--code",
                      "System.GC.Collect(); System.GC.WaitForPendingFinalizers();"
                      "System.GC.Collect(); return \"gc\";"], timeout_s=90)
    if not gc_r.success:
        raise RuntimeError(f"地板复核 GC 失败: {gc_r.errors[:1]}")
    time.sleep(2)
    post = poll_median()
    run_unity(["command", "editor.playmode stop", "--project-path", proj], timeout_s=60)
    time.sleep(4)
    return {k: {"pre_gc": pre[k], "post_gc": post[k]} for k in keys
            if pre.get(k) is not None and post.get(k) is not None}


def resample(fixture: str, tag: str, windows: int = 4, timeout_s: int = 1500) -> dict:
    """当场重采一个束（full 会话 = A+A2，与标定同协议）。

    前置检查（实测教训）：常驻件在不在就在那儿，不在别硬采——
    A2 注入失败 m1_multiwin 自身会返 error，这里只透传并拒绝空束。
    驱动器防御（第二轮彩排教训）：采样窗不是回放。驱动器停在武装态时
    每帧压 targetFrameRate=60，会把 m1_multiwin 自设的 uncapped 形态
    覆盖掉——F-CPU-01 的每帧分配被 vsync 等待帧从 78KB 抬到 183KB，
    被测机制淹死在仪器行为里。采前显式 disarm，仪器状态归采样方。"""
    rp_ctl("disarm")
    out_dir = ROOT / "runs" / tag
    cmd = [sys.executable, str(ROOT / "scripts" / "m1_multiwin.py"),
           "--session", "full", "--fixtures", fixture,
           "--windows", str(windows), "--caldir", str(out_dir)]
    # 采前状态：第一轮彩排作废的根因就是没人记这个——驱动器采前是死的
    # （被 overhead L0 unload 摘钩）、采中途被编译域重载复活，两束形态
    # 不同却无人知晓。pre/mid 都记录，一致性交给 assert_comparable 判。
    pre = instrument_state()
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
    # 仪器状态写进束数据（彩排第一轮教训的固化）：
    #   _instr_pre  —— 发起前状态，决定是否要先重装常驻件再采
    #   _instr_mid  —— 窗口实际运行中的状态，才是对比基准
    # mid≠pre ⇒ 采样中途常驻件被域重载复活，这束数据单独标记，不静默
    mid = instrument_state()
    d["_instr_pre"], d["_instr_mid"] = pre, mid
    # 采前≠采中 ⇒ 常驻件在采样中途被域重载复活/摘钩（第一轮彩排实测撞死
    # 的正是这条：pre=uncapped 驱动器死、mid=capped60 驱动器活）。这束数据
    # 的窗口内部状态不齐，必须显式标记，判定方按"整束作废"处理。
    if pre.get("fps_mode") != mid.get("fps_mode"):
        d["_instr_drift"] = f"pre={pre.get('fps_mode')} mid={mid.get('fps_mode')}"
    if mid["fps_mode"] == "capped60" and len(fx["windows"]) >= 2:
        # 封顶态下 cpuFrameTime 的窗口间 σ≈0（钉在 16.6），gc 尖峰可能被
        # 60fps 等待吃掉——如实记录，判定用 G17 绝对门限路径
        d["_capped_warning"] = True
    return d


def bundle_stats(doc: dict, fixture: str) -> dict:
    fx = doc["fixtures"][fixture]
    return stats_from_windows(fx)


def verdict_from_bundles(det_id: str, fixture: str,
                        before_doc: dict, after_doc: dict,
                        compile_ok: bool,
                        floor: Optional[dict] = None,
                        floor_before: Optional[dict] = None) -> tuple[Verdict, dict]:
    """核心判定。返回 (Verdict, 详情 dict——详情供 trace/report 落盘)。

    记忆体守护（FRESH_POST_GC_METRICS，见 upi/core/memory_guard.py）用
    **两侧各自**的 fresh post-GC 值对比：
      floor         = 改后态 fresh（调用方在修复态采好传入）
      floor_before  = 改前态 fresh（调用方在缺陷态采好传入）
    任一侧缺 ⇒ 该指标 value_source=fresh_missing = **未评估**，进
    detail["memory_guards_unevaluated"] 单列（G20 盲区）——绝不拿被回收时点
    污染的窗口值冒充判定。普通（时间/渲染计数）指标仍用 bundle 窗口中位。
    本函数不开场景不进 Play，保持纯判定无副作用。"""
    # 仪器状态双闸（第一轮彩排作废教训的固化）：
    #   漂移闸——单束内 _instr_pre ≠ _instr_mid，束内窗口状态不齐，整束作废；
    #   对比闸——两束 mid 形态/常驻件存活不一致，禁止对比（G7 引申）。
    for doc, tag in ((before_doc, "before"), (after_doc, "after")):
        pre_s, mid_s = doc.get("_instr_pre"), doc.get("_instr_mid")
        if pre_s and mid_s and pre_s.get("fps_mode") != mid_s.get("fps_mode"):
            raise RuntimeError(
                f"{tag} 束仪器状态中途漂移（采前={pre_s.get('fps_mode')} "
                f"采中={mid_s.get('fps_mode')}），整束作废重采，不得带病判定")
    bp, ap = before_doc.get("_instr_mid"), after_doc.get("_instr_mid")
    if bp and ap:
        assert_comparable(bp, ap)

    det = next(d for d in DEFAULT_REGISTRY if d.id == det_id)
    cal = latest_calibration()
    if cal:
        apply_calibration([det], cal)        # 阈值只认标定值（§8.1）
    bstats = bundle_stats(before_doc, fixture)
    astats = bundle_stats(after_doc, fixture)

    # 播放期形态闸（第一轮作废的自我修正）：_instr_* 在 Play 结束后才取样，
    # 测的是编辑态形态——抓不住"窗口运行中途被域重载复活"的差异。
    # 形态的真证据在窗口数据本身：capped60 的 cpuFrameTimeMs 中位 ≈16.6
    # 且窗口间 σ≈0；uncapped ≈1.9。阈值 12ms 只是形态分界（两态相差近十倍，
    # 落在界内的真实性能变化在本 demo 不存在——如实标注这是启发式）。
    def _regime(stats):
        v = stats.get("cpuFrameTimeMs", {}).get("median")
        if v is None:
            return "unknown"
        return "capped60" if v >= 12.0 else "uncapped"
    rb, ra = _regime(bstats), _regime(astats)
    if rb != ra and "unknown" not in (rb, ra):
        raise RuntimeError(
            f"播放期帧率封顶形态不一致：before={rb}({bstats['cpuFrameTimeMs']['median']:.2f}ms) "
            f"after={ra}({astats['cpuFrameTimeMs']['median']:.2f}ms)。所有时间类指标差值"
            "混入仪器形态变化，整轮作废重采（第一轮彩排实测教训）")

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
    memory_unevaluated = []   # G20 盲区：记忆体守护没采到两侧 fresh 就是未评估
    for key, bv in sorted(bstats.items()):
        if key in NOT_GUARDED or key == m:
            continue
        av = astats.get(key)
        if av is None:
            continue

        # ── 分支一：记忆体类指标 ⇒ fresh post-GC 口径（两侧都要有）
        # 窗口值受回收时点污染（a4_altertest 实测 7.8MB 假象/1.9MB 净代价），
        # 拿它判劣化 = 拿已知失真数据下结论；判不了就如实登记盲区，不冒充评估过。
        if key in FRESH_POST_GC_METRICS:
            entry = memory_guard_entry(key, bv, av, floor_before, floor)
            if entry["value_source"] == "fresh_post_gc" and entry["cross_check_ok"]:
                reg = is_regressed(entry)
                guards.append(GuardMetric(
                    metric=key,
                    before=round(entry["value_before"], 3),
                    after=round(entry["value_after"], 3),
                    delta=round(entry["delta"], 3), regressed=reg,
                    value_source="fresh_post_gc", basis=entry["basis"],
                    gc_floor_check={
                        "before_side": (floor_before or {}).get(key),
                        "after_side": (floor or {}).get(key),
                        "window_delta_archive": round(entry["bundle_delta"], 3)}))
                if reg:
                    worst = max(worst, abs(entry["delta"]) / max(
                        abs(entry["value_before"]), 1e-9))
            else:
                memory_unevaluated.append({
                    "metric": key, "value_source": entry["value_source"],
                    "cross_check_ok": entry["cross_check_ok"], "basis": entry["basis"],
                    "window_before": round(bv["median"], 3),
                    "window_after": round(av["median"], 3),
                    "window_delta": round(av["median"] - bv["median"], 3)})
            continue

        # ── 分支二：普通指标（时间/渲染计数）⇒ bundle 窗口中位对比
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
                                  delta=round(delta, 3), regressed=reg,
                                  value_source="window"))
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
              "conflicts": [c.model_dump() for c in conflicts],
              "memory_guards_unevaluated": memory_unevaluated}
    return v, detail
