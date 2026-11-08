#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆体守护新口径单测（a4_altertest 实测数据输入，零联网零 Editor）。

输入数据全部来自实测：
- M3 彩排束实况：before 窗口 104.7MB / after 窗口 115.3MB /
  改后态 fresh pre=118.5 post=115.7（runs/m3_rehearsal_20261108_164724）
- A4 交替块 A2/B2：窗口 [104.3,105.4] 与 [111.5,111.9]，
  floor pre/post 对应 105.9/104.4 与 109.2/106.4（summary.json）

判据（memory_guard.py 固化的口径）：
  两侧 fresh 齐 + 交叉校验过 + post_gc 上升 >2% ⇒ regressed
  任一侧缺 ⇒ fresh_missing = 未评估（盲区，不拿污染窗口值冒充判定）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.core.memory_guard import (FRESH_POST_GC_METRICS,  # noqa: E402
                                   FRESH_TOLERANCE, is_regressed,
                                   memory_guard_entry)

MB = 1024 * 1024
PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
        print(f"   [ok] {name}")
    else:
        FAIL.append(name)
        print(f"   [FAIL] {name}")


def stat(med):
    return {"median": med, "noise_band_mad": 0.0, "degenerate_distribution": True}


# ── 1. 改前态 fresh 缺失 ⇒ 未评估（M3 第四轮实况）──────────────────
f_after = {"monoUsedBytes": {"pre_gc": 118.5 * MB, "post_gc": 115.7 * MB}}
e = memory_guard_entry("monoUsedBytes", stat(115.3 * MB), stat(116.0 * MB),
                       None, f_after)
check("改前态缺 fresh ⇒ value_source=fresh_missing",
      e["value_source"] == "fresh_missing")
check("未评估 ⇒ is_regressed False（不拿污染窗口值判）", not is_regressed(e))
check("窗口差值仍留档（bundle_delta 归档不丢数据）",
      abs(e["bundle_delta"] - 0.7 * MB) < 1)

# ── 2. 两侧 fresh 齐、post_gc 差在容差内 ⇒ 不劣化（A2→B2 配对实况）──
# A2: 窗口[104.3,105.4] floor pre/post 105.9/104.4；B2: 窗口[111.5,111.9] 109.2/106.4
f_b = {"monoUsedBytes": {"pre_gc": 105.9 * MB, "post_gc": 104.4 * MB}}
f_a = {"monoUsedBytes": {"pre_gc": 109.2 * MB, "post_gc": 106.4 * MB}}
# 彩排实测教训（2026-11-08 新口径首轮）：fresh 是独立 Play 会话，pre_gc 与
# 验证窗束天然差 5~10%（当日 6.3%）——旧 2% 硬交叉校验把合法数据误推盲区。
# 新语义：≤25% 一律采信（记偏差留档），>25% 才按"来自别的机器状态"拒收。
e = memory_guard_entry("monoUsedBytes", stat(104.85 * MB), stat(111.7 * MB), f_b, f_a)
check("pre_gc 偏差 1.0%/2.2% 在防抖内 ⇒ 采信", e["cross_check_ok"] is True)
check("偏差值留档（staleness_dev 进报告可回溯）",
      e["staleness_dev"]["before"] is not None)
check("post_gc 104.4→106.4 = +1.9% < 2% 容差 ⇒ 判非劣化（净常驻属容差内波动）",
      not is_regressed(e))

# 拒收分支：pre_gc 与窗口中位差 40% ⇒ 疑似陈旧/别机，cross_check_ok=False
f_stale = {"monoUsedBytes": {"pre_gc": 150.0 * MB, "post_gc": 106.4 * MB}}
e2 = memory_guard_entry("monoUsedBytes", stat(104.85 * MB), stat(111.7 * MB),
                        f_b, f_stale)
check("pre_gc 偏差 40% ⇒ 防抖拒收", e2["cross_check_ok"] is False)
check("拒收 ⇒ 不判劣化也不判清白（未评估语义）", not is_regressed(e2))
check("post_gc +1.9% < 2% ⇒ 判非劣化（净常驻 1.9MB 属容差内波动）",
      not is_regressed(e))

# ── 3. post_gc 上升超容差 ⇒ 真劣化照抓（构造：净涨 5%）─────────────
f_a3 = {"monoUsedBytes": {"pre_gc": 105.1 * MB, "post_gc": 109.6 * MB}}
e = memory_guard_entry("monoUsedBytes", stat(105.0 * MB), stat(105.0 * MB),
                       f_b, f_a3)
check("post_gc +5% ⇒ regressed（口径升级不放松抓真回归）", is_regressed(e))

# ── 4. 非记忆体指标不进这套（普通指标仍走窗口对比）─────────────────
check("时间/渲染类指标不在 FRESH_POST_GC_METRICS",
      not ({"cpuFrameTimeMs", "drawCalls", "triangles"} & set(FRESH_POST_GC_METRICS)))
check("四类记忆体计数器全在集合内",
      set(FRESH_POST_GC_METRICS) == {"monoUsedBytes", "totalAllocatedBytes",
                                     "totalReservedBytes", "monoHeapBytes"})

# ── 5. fresh 有 pre/post 但缺 post_gc 字段 ⇒ 同样未评估 ────────────
e = memory_guard_entry("monoUsedBytes", stat(105.0 * MB), stat(105.0 * MB),
                       {"monoUsedBytes": {"pre_gc": 105.0 * MB}},
                       {"monoUsedBytes": {"post_gc": 106.0 * MB}})
check("缺 post_gc ⇒ fresh_missing（不猜值）", e["value_source"] == "fresh_missing")

print(f"\n==== memory_guard 单测: {PASS} 通过, {len(FAIL)} 失败"
      + (f" {FAIL}" if FAIL else ""))
sys.exit(1 if FAIL else 0)
