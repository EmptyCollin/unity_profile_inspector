"""v0.12 契约负例测试（AGENTS G18/G19/G20、D15/D17 的代码层落实）。

运行：C:/Python314/python.exe scripts/test_contracts_v012.py
每条断言 = 一个"必须被构造期拒收"的越界形状 + 一个"合规形状必须放行"的对照。
负例必须用**重新构造**验证（pydantic model_copy 不重跑校验器——本轮实测踩过）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from upi.core import models
from upi.core.detector import DEFAULT_REGISTRY, Detector, Thresholds

PASS: list[str] = []
FAIL: list[str] = []


def expect_reject(name: str, fn) -> None:
    try:
        fn()
        FAIL.append(f"{name}: 未拦截（违规对象被放行）")
    except ValidationError:
        PASS.append(name)


def expect_accept(name: str, fn) -> None:
    try:
        fn()
        PASS.append(name)
    except ValidationError as e:
        FAIL.append(f"{name}: 合规形状被误伤 → {e.errors()[0]['msg'][:80]}")


# ── 基线：旧注册表不得被新校验误伤；构成随 M2a/T2w 更新 ──────────
expect_accept("注册表存量 detector 照常构造", lambda: DEFAULT_REGISTRY)
_wl = [d for d in DEFAULT_REGISTRY if d.ptype == "waterline"]
_ep = [d for d in DEFAULT_REGISTRY if d.ptype == "episode"]
assert len(_wl) == 4, f"水位条目变了：{len(_wl)}"
assert [d.id for d in _ep] == ["DET-LOAD-FRAMESPIKE"], f"发作条目构成异常：{_ep}"
# 阈值体系按 ptype 分流：水位只认噪声标定，发作只认带出处的帧预算锚点
assert all(d.thresholds.threshold_source == "noise_calibration" for d in _wl)
assert all(d.thresholds.threshold_source == "frame_budget" and d.thresholds.budget_ref
           for d in _ep), "episode 检测器必须用 frame_budget 阈值且带 budget_ref"

ev_a = models.Evidence(tool="poll", value=127.0, unit="calls", data_tier="A")
ev_a2 = models.Evidence(tool="probe", value=2e5, unit="B/frame", data_tier="A2")
sig = models.IssueSignature(dim="m", metric="m", mechanism="mech_x", threshold=1.0)

# ── G19：episode 必须逐帧源；渲染计数禁注 GPU 逐笔 spike ─────────
DET_BASE = dict(id="DET-T", link="gpu", subject="F", unit="ms",
                thresholds=Thresholds(min_magnitude=16.7))


def rej_episode_on_a():
    Detector(metric="drawCalls", source_tier="A", ptype="episode", **DET_BASE)


def rej_episode_on_c():
    Detector(metric="someStatic", source_tier="C", ptype="episode", **DET_BASE)


def rej_drawcalls_a4():
    # #36 改判：渲染合法源是 {A, A2}，A4 不在其中（FrameTimingManager 不产渲染计数）
    # ⇒ 谎称 A4 仍拒，但拒因从旧条文的"渲染无逐帧源"换成"A4 非渲染合法源"。
    Detector(metric="drawCalls", source_tier="A4", ptype="episode",
             attribution_source="human", **DET_BASE)


def ok_render_a2_episode_human():
    # 解禁的正向证据（exp5/exp6：真名带 Count 后缀 + 双条件定位，A2 逐帧与轮询同数量级）
    # ⇒ 渲染计数 spike detector 现在合法，但归因必须 human（FD 程序侧不可解读未被推翻）
    Detector(**{**DET_BASE, "metric": "drawCalls", "source_tier": "A2",
                "ptype": "episode", "attribution_source": "human"})


def rej_render_a2_episode_auto():
    # 检出事实 ≠ 归因能力：渲染 spike 无程序化逐笔归因源，auto 即虚报检出能力（I1）
    Detector(**{**DET_BASE, "metric": "drawCalls", "source_tier": "A2",
                "ptype": "episode", "attribution_source": "auto"})


def rej_batches_a2_waterline():
    # G16②：Render 类目无汇总 "Batches Count"（只 Dynamic/Instanced/Static 三分项）
    # ⇒ batches 唯一口径是轮询 A，注入侧分项禁止冒充汇总
    Detector(**{**DET_BASE, "metric": "batches", "source_tier": "A2"})


def ok_batches_a_waterline():
    Detector(**{**DET_BASE, "metric": "batches", "source_tier": "A"})


def ok_gpu_a4_episode_human():
    Detector(metric="gpuFrameTimeMs", source_tier="A4", ptype="episode",
             attribution_source="human", link="gpu", id="DET-T", subject="F",
             unit="ms", thresholds=Thresholds(min_magnitude=16.7))


def rej_gpu_a4_episode_auto():
    Detector(metric="gpuFrameTimeMs", source_tier="A4", ptype="episode",
             attribution_source="auto", link="gpu", id="DET-T", subject="F",
             unit="ms", thresholds=Thresholds(min_magnitude=16.7))


for nm, f in [("G19 episode-on-A 拒收", rej_episode_on_a),
              ("G19 episode-on-C 拒收", rej_episode_on_c),
              ("G16① drawCalls 谎称 A4 拒收（合法源仅 A/A2）", rej_drawcalls_a4),
              ("G16② batches A2 水位拒收（无汇总句柄）", rej_batches_a2_waterline),
              ("#36 渲染 A2 发作 auto 归因拒收（无源必须 human）", rej_render_a2_episode_auto),
              ("G19 GPU episode 自动归因拒收（无源必须 human）", rej_gpu_a4_episode_auto)]:
    expect_reject(nm, f)
expect_accept("G19 GPU-A4 发作 detector(human 归因) 放行", ok_gpu_a4_episode_human)
expect_accept("#36 渲染 A2 发作 detector(human 归因) 放行（spike 解禁）", ok_render_a2_episode_human)
expect_accept("#36 batches A 水位放行（轮询唯一口径）", ok_batches_a_waterline)

# ── G18：finding 等级归属 ─────────────────────────────────────────
pot = models.PotentialFinding(
    pattern="per_frame_heap_alloc_in_hot_path", location="Assembly-CSharp: CombatSkillManager",
    slope_source=ev_a2, scale_assumption="实例到 400 时预测 80KB/帧破 16.7ms 预算线",
    link="cpu")

expect_accept("potential 三字段齐 构造放行", lambda: pot)


def rej_pot_missing_slope():
    models.PotentialFinding(pattern="p", location="l", scale_assumption="s", link="cpu")  # type: ignore[call-arg]


expect_reject("G18 potential 缺 slope_source 拒收", rej_pot_missing_slope)


def rej_pot_as_issue():
    models.Finding(kind="potential", issue=models.Issue(
        signature=sig, link="cpu", evidence=[ev_a2], magnitude=1, deviation_ratio=2))


expect_reject("G18 potential 携带 Issue 载荷拒收", rej_pot_as_issue)
expect_accept("issue/potential 各归其位", lambda: (
    models.Finding(kind="issue", issue=models.Issue(
        signature=sig, link="cpu", evidence=[ev_a2], magnitude=1, deviation_ratio=2)),
    models.Finding(kind="potential", potential=pot)))

# ── A4 哨兵规则 + BreachSample 深度（D17）+ 可复现（T2） ─────────
expect_reject("A4 未回填帧(gpu=0,ts=0) 拒收", lambda: models.A4Frame(
    frame_idx=1, cpu_frame_time_ms=1.0, gpu_frame_time_ms=0.0, complete_ts=0))
expect_accept("A4 正常帧放行", lambda: models.A4Frame(
    frame_idx=1, cpu_frame_time_ms=1.6, gpu_frame_time_ms=1.8, complete_ts=448803535218))


def sample(**kw) -> models.BreachSample:
    d = dict(frame_idx=5000, metric="gc_alloc_per_frame", value=2e5, threshold_abs=1e5,
             deviation_ratio=2.0, ring_before=[1e4] * 10, ring_after=[1e4] * 14,
             data_tier="A2", replay_seed=7, protocol_fingerprint="fp1")
    d.update(kw)
    return models.BreachSample(**d)


expect_reject("D17 ring_after=10<14 拒收", lambda: sample(ring_after=[1e4] * 10))
expect_accept("D17 ring_after=14 放行", sample)
b = sample(replay_seed=-1)
assert b.replayable() is False, "seed=-1 应判不可复现（允许落盘但禁进修复循环）"
assert sample().replayable() is True
PASS.append("T2 replayable() 判据（seed=-1 记录但不进循环）")

# ── Issue.ptype=episode 的证据侧校验 ─────────────────────────────
expect_reject("Issue episode 证据全为 A 档拒收", lambda: models.Issue(
    signature=sig, link="cpu", evidence=[ev_a], magnitude=1, deviation_ratio=2,
    ptype="episode", attribution_source="human"))
expect_accept("Issue episode 含 A2 证据放行", lambda: models.Issue(
    signature=sig, link="cpu", evidence=[ev_a2], magnitude=1, deviation_ratio=2,
    ptype="episode", attribution_source="human"))

# ── 压测档字段（D15/G7） ─────────────────────────────────────────
p1 = models.SamplingProtocol()
p2 = models.SamplingProtocol(scale_factor=100.0)
assert p1.scale_factor == 1.0 and p2.scale_factor == 100.0
fp1 = models.Fingerprint(scene_guid="g", replay_script_hash="h", quality_tier="high",
                         warmup_samples=8, window_samples=20, n_samples=4, poll_rate=1.0,
                         data_tier="A", environment_fingerprint="e", scale_factor=1.0)
fp2 = fp1.model_copy(update={"scale_factor": 10.0, "graphics_api": "D3D11"})
same, diffs = fp1.same_for_compare(fp2)
assert not same and any("scale_factor" in d for d in diffs) and any("graphics_api" in d for d in diffs)
PASS.append("D15/G7 scale_factor 与 graphics_api 入指纹后常态≠压测不可比")

# ── #30 整束渲染数据档：G16④ 从纸面闸变真闸的判据与选源一致性 ─────
from upi.core.render_source import bundle_render_tier
from scripts.m1_detect import stats_by_fixture, stats_from_windows


def _rw(draw=None, sent=None, poll=10.0, median_form=False):
    """构造一窗：poll=轮询 drawCalls 中位，draw=A2 渲染序列（或摘要 *_median 形）。"""
    a2 = {}
    if median_form:
        if draw is not None:
            a2 = {"draw_median": draw, "draw_n": 60,
                  "setpass_median": 5.0, "setpass_n": 60, "tris_median": 9.0, "tris_n": 60}
    elif draw is not None:
        a2 = {"draw": [draw] * 60, "setpass": [5.0] * 60, "tris": [9.0] * 60}
    w = {"metrics": {"drawCalls": {"median": poll}}, "a2": a2}
    if sent is not None:
        w["sentinel"] = sent
    return w


_OK = {"draw": "ok", "setpass": "ok", "tris": "ok"}


def _mk(wins):
    return {"F": {"windows": wins}}


# ① 旧束/none/idle（无渲染 A2 序列）⇒ A，检测逐字回退轮询（不作废历史）
_t = bundle_render_tier(_mk([_rw(poll=10.0) for _ in range(4)]))
_v = stats_by_fixture({"fixtures": _mk([_rw(poll=10.0) for _ in range(4)])})["F"]["drawCalls"]["median"]
assert _t == "A" and _v == 10.0, f"旧束应标 A 且用轮询值，实得 {_t}/{_v}"
PASS.append("#30 旧束无渲染A2序列⇒标A且检测用轮询（历史不作废）")

# ② 全窗哨兵 ok ⇒ A2，检测采用 A2 逐帧中位（100≠轮询10，可分辨）
_t = bundle_render_tier(_mk([_rw(draw=100.0, sent=_OK) for _ in range(4)]))
_v = stats_by_fixture({"fixtures": _mk([_rw(draw=100.0, sent=_OK) for _ in range(4)])})["F"]["drawCalls"]["median"]
assert _t == "A2" and _v == 100.0, f"哨兵全过应标A2且用A2值，实得 {_t}/{_v}"
PASS.append("#30 哨兵全过⇒标A2且检测采用A2逐帧值")

# ③ 任一窗哨兵没过 ⇒ 整束标 A，检测回退轮询（fail-loud 的选源侧落地）
_mixed = [_rw(draw=100.0, sent=_OK), _rw(draw=100.0, sent=_OK),
          _rw(draw=0.0, sent={"draw": "a2_zero poll=10.0", "setpass": "ok", "tris": "ok"}),
          _rw(draw=100.0, sent=_OK)]
_t = bundle_render_tier(_mk(_mixed))
_v = stats_by_fixture({"fixtures": _mk(_mixed)})["F"]["drawCalls"]["median"]
assert _t == "A" and _v == 10.0, f"一窗没过应整束降A并回退轮询，实得 {_t}/{_v}"
PASS.append("#30 任一窗哨兵没过⇒整束标A并回退轮询（all-or-nothing）")

# ④ 跨机制不一致（clean 全过、某缺陷一窗没过）⇒ 整束 A：杜绝"clean走A2、缺陷走轮询"跨源
_cross = {
    "CLEAN": {"windows": [_rw(draw=100.0, sent=_OK) for _ in range(4)]},
    "F-GPU-01": {"windows": [_rw(draw=100.0, sent=_OK),
                              _rw(draw=0.0, sent={"draw": "ratio_out", "setpass": "ok", "tris": "ok"}),
                              _rw(draw=100.0, sent=_OK), _rw(draw=100.0, sent=_OK)]}}
assert bundle_render_tier(_cross) == "A", "跨机制有一窗没过必须整束降 A"
PASS.append("#30 clean全过但缺陷一窗没过⇒整束降A（同一次compare不可能跨源）")

# ⑤ 序列在场但 sentinel 字段缺失（idle-only 没跑对表）⇒ 不假装通过，A
assert bundle_render_tier(_mk([_rw(draw=100.0, sent=None) for _ in range(4)])) == "A"
PASS.append("#30 有A2序列但无sentinel字段⇒不假装通过判A（对不了表就不采用）")

# ⑥ 标签只降不升的接缝验证：stats_from_windows 显式传 "A" 时绝不采 A2（缺省安全）
_v = stats_from_windows({"windows": [_rw(draw=100.0, sent=_OK) for _ in range(4)]}, "A")["drawCalls"]["median"]
assert _v == 10.0, f"render_tier=A 必须用轮询，实得 {_v}"
PASS.append("#30 stats_from_windows 缺省A⇒漏传调用方也不会误采可疑A2")

# ── 结果 ─────────────────────────────────────────────────────────
print(f"通过 {len(PASS)} 项：")
for x in PASS:
    print("  ✓", x)
if FAIL:
    print(f"失败 {len(FAIL)} 项：")
    for x in FAIL:
        print("  ✗", x)
    raise SystemExit(1)
print("契约负例测试全部通过")
