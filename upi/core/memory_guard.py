"""记忆体守护的正确口径（A4 拆弹结论，DESIGN §2.4 / AGENTS §1.5）。

问题（M3 三轮彩排 + runs/a4_altertest_20261108_183910 实测）：
monoUsedBytes / totalAllocatedBytes 这类计数器量的是"**此刻有多少还没回收**"，
不是"这段代码吃了多少"。修复把每帧分配清零后 GC 触发频率骤降，编辑器自身
与观测管线产生的临时垃圾开始积累 ⇒ 观察窗内读数上升，而净常驻只涨 ~2MB
（实测 Δ修复 7.8MB 里只有 1.9MB 是净常驻）。拿被污染的窗口值判守护，
会把"回收时机变化"读成"内存回归"；反之放宽阈值又会漏掉真回归。

实测证据（同仓库、交替块 A1→B1→A2→B2，见 summary.json）：
  Δ修复(窗口值) = 7.82MB   Δ地板(强制GC后) = 1.94MB   缺陷块漂移 = 0.69MB
  单次 GC 可回收：修复块 2.79MB > 缺陷块 1.44MB ⇒ 修复态不是"垃圾更多"，
  是"同样垃圾、回收时点被推后"。

口径（本模块固化）：
  FRESH_POST_GC_METRICS 里的指标，守护对比只用 **fresh_window 的 post_gc 值**
  （进 Play→采一段→强制 GC→再采一段，取后一段中位），两侧同做（改前态
  一份、改后态一份）。post_gc 是"净常驻"的可观测代理，不受回收时点污染。
  bundle 窗口值只作交叉校验与留档，不参与劣化判定。

fail-closed：任一侧 fresh 缺失时**不退回窗口值判劣化**（那是被污染口径，
判定它等于拿已知失真数据下结论）——该指标标 value_source="fresh_missing"、
regressed=False 但 basis 里显式声明"记忆体守护未评估"，进报告单列，
评审时按盲区处理（G20 精神）。绝不允许静默用污染值冒充评估过。
"""
from __future__ import annotations

from typing import Optional

# 受回收时点污染、必须用 fresh post-GC 口径对比的记忆体计数器
# （实测依据：probe/gc_floor.cs 编辑态响应性 + a4_altertest 交替块）
FRESH_POST_GC_METRICS = frozenset({
    "monoUsedBytes", "totalAllocatedBytes", "totalReservedBytes", "monoHeapBytes",
})

# fresh post_gc 两侧对比容差：比 GUARD_TOLERANCE(1%) 宽——fresh_window 是独立
# 播放会话，跨会话差异本就大于同会话窗口差异；实测跨块漂移 0.69/103≈0.7%，
# 2% 留 3 倍余量。
FRESH_TOLERANCE = 0.02

# 粗防抖容差（原"交叉校验"，2026-11-08 彩排实测后降级）：pre_gc 与同侧
# bundle 窗口中位**不该要求吻合**——fresh 是独立 Play 会话（重开场景、重新
# 进 Play、会话年龄不同），与验证窗束天然差 5~10%（当日实测 6.3%）。
# 旧版拿 2% 当硬闸，把合法数据误判成盲区（教训 23）。现在只做粗防抖：
# 偏差 >25% 视为"这份 fresh 根本来自别的机器状态/陈旧文件"，拒收；
# 正常范围记 cross_check_ok=True 并保留偏差值供报告留档。
GROSS_STALENESS_TOLERANCE = 0.25


# GC 响应性下限（真机 resume 实测撞出的口径修正）：post_gc 与 pre_gc 的相对
# 变化小于此值 ⇒ 该计数器**不受 GC 影响**，"post_gc = 净常驻代理"的前提不成立，
# 两侧读数只是会话累积量（受域重载/编译/编辑器重启次数支配），跨会话不可比。
# 实测依据（runs/patrol_20261109_105554 改前侧）：
#   monoUsedBytes      103.47MB -> 101.73MB  相对 -1.68%   响应
#   monoHeapBytes      156.99MB -> 156.99MB  相对  0.00%   不响应
#   totalAllocatedBytes 672.81MB -> 672.84MB 相对 +0.005%  不响应
#   totalReservedBytes 同值不响应
# 与 11-08 probe/gc_floor.cs 的编辑态实测（三类 delta=0）完全一致——当时已记下
# 该事实，却仍把这四类一起放进取值集合，丢了区分（本轮 43% 假劣化的根因）。
GC_UNRESPONSIVE_REL = 0.002


def _rel_delta(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-9)


def memory_guard_entry(metric: str, bstat: dict, astat: dict,
                       fresh_before: Optional[dict],
                       fresh_after: Optional[dict]) -> dict:
    """单个记忆体守护指标的取值。返回：
    {value_before, value_after, delta, basis, value_source,
     cross_check_ok, bundle_delta(留档)}

    fresh_* 为 fresh_window/gc_floor_measure 输出中该指标的
    {"pre_gc":x,"post_gc":y}；任一侧 None 或缺 post_gc ⇒ fresh_missing。
    """
    b_win, a_win = bstat["median"], astat["median"]
    fb = (fresh_before or {}).get(metric)
    fa = (fresh_after or {}).get(metric)
    entry = {"bundle_before": b_win, "bundle_after": a_win,
             "bundle_delta": a_win - b_win}          # 仅留档，不参与判定
    if not fb or not fa or fb.get("post_gc") is None or fa.get("post_gc") is None:
        entry.update(value_before=None, value_after=None, delta=None,
                     value_source="fresh_missing", cross_check_ok=None,
                     basis="fresh post-GC 数据缺失——记忆体守护未评估（盲区，进报告单列）")
        return entry
    # 粗防抖：pre_gc 与同侧窗口中位偏差 >25% 才拒收（跨会话天然差 5~10%，
    # 2% 硬闸已被当日彩排实测证明过严——它把合法数据推进盲区，等于自废守护）
    cc_b = _rel_delta(fb["pre_gc"], b_win) <= GROSS_STALENESS_TOLERANCE
    cc_a = _rel_delta(fa["pre_gc"], a_win) <= GROSS_STALENESS_TOLERANCE
    vb, va = float(fb["post_gc"]), float(fa["post_gc"])
    # GC 无响应闸（本轮真机实测撞出）：pre_gc≈post_gc 说明强制 GC 没能动这个
    # 计数器 ⇒ 它不是"未回收量"而是"会话累积量"，post_gc 不再是净常驻代理。
    # 两侧同判：任一侧响应就仍有信息量；两侧都不响应 ⇒ 该指标跨会话不可比，
    # 降级为未评估盲区（G20），既不判劣化也不判清白——上一轮 resolved 是
    # 编辑器开久了堆顶格两侧同值的巧合，不是口径正确。
    resp_b = _rel_delta(fb["post_gc"], fb["pre_gc"]) > GC_UNRESPONSIVE_REL
    resp_a = _rel_delta(fa["post_gc"], fa["pre_gc"]) > GC_UNRESPONSIVE_REL
    if not (resp_b or resp_a):
        entry.update(value_before=None, value_after=None, delta=None,
                     value_source="gc_unresponsive", cross_check_ok=None,
                     basis=f"强制 GC 前后两侧读数变化均 ≤{GC_UNRESPONSIVE_REL:.1%}"
                           "（该计数器不受回收影响=会话累积量，跨会话不可比）"
                           "——记忆体守护未评估（盲区，进报告单列）；"
                           "窗口差值仅留档不参与判定")
        return entry
    dev_b = round(_rel_delta(fb["pre_gc"], b_win), 4)
    dev_a = round(_rel_delta(fa["pre_gc"], a_win), 4)
    entry.update(value_before=vb, value_after=va, delta=va - vb,
                 value_source="fresh_post_gc",
                 cross_check_ok=cc_b and cc_a,
                 staleness_dev={"before": dev_b, "after": dev_a},
                 gc_responsive={"before": resp_b, "after": resp_a},
                 basis=f"post_gc 口径（净常驻代理），容差 {FRESH_TOLERANCE:.0%}；"
                       f"pre_gc 防抖偏差 before={dev_b:.1%} after={dev_a:.1%}"
                       f"；GC 响应侧={'改前' if resp_b else ''}"
                       f"{'改后' if resp_a else '' or '无'}"
                       + ("" if cc_b and cc_a else " !!超 25%—fresh 疑似来自别的机器状态"))
    return entry


def is_regressed(entry: dict) -> bool:
    """劣化判据：仅当两侧 fresh 齐、交叉校验过、post_gc 上升超容差。
    fresh_missing / 校验不过 ⇒ 不判劣化也不判清白——未评估，调用方单列。"""
    if entry.get("value_source") != "fresh_post_gc" or not entry.get("cross_check_ok"):
        return False
    return bool(entry.get("delta", 0) > 0
                and _rel_delta(entry["value_after"], entry["value_before"])
                > FRESH_TOLERANCE)
