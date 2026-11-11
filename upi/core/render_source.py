"""整束渲染计数"用 A2 逐帧还是回退轮询"的单一数据档判据（#30，G16①/④ 落地）。

为什么必须**整束级**（不是逐机制、不是逐窗）：一次 compare() 拿 clean 窗口与缺陷
窗口对同一指标比。若判据逐机制各算各的，会出现"clean 过了哨兵取 A2、某缺陷机制
哨兵没过取轮询"——这就是跨源比较（G16④ 要拦的），而 exp6 实测两源比值 1.00，
后果数值上几乎看不出来，属于最危险的静默失真。所以一个 bundle 只产出一个渲染
数据档，clean 与每个缺陷、检测与阈值生成全部读这一个值，结构上不可能不一致。

判据：束内**所有**携带渲染 A2 序列的窗口、对**每个**渲染序列，哨兵都报 ok 才整体
"A2"；任一处没 ok（含"序列在场但 sentinel 字段缺失"=idle-only 没跑对表）⇒ 整束
"A"。旧束/none/idle 会话根本没有渲染 A2 序列 ⇒ 没有任何窗参与判定 ⇒ 返回 "A"
（与其实情一致：那批数据就是纯轮询，标 A 是唯一诚实的值）。

消费者（都从同一份 fixtures 现算，天然同值）：
  - m1_multiwin.main：采完盖章 doc["protocol"]["data_tier"]（进 G7 指纹的数据侧）；
  - m1_multiwin.do_merge：渲染阈值带按它选 A2 或轮询（与检测同源）；
  - m1_detect.stats_from_windows：检测值按它选 A2 或轮询（clean 与缺陷同参数）。
batches 不在 RENDER 键里：句柄全量表证实 Render 无汇总 "Batches Count"（G16②），
它永远只有轮询口径，不参与本判据。
"""
from __future__ import annotations

# A2 序列键 → 判定指标名（batches 不在内：Render 无汇总 Count 句柄，G16②）
RENDER_A2MAP = {"draw": "drawCalls", "setpass": "setPassCalls", "tris": "triangles"}


def _has_render_seq(win: dict) -> bool:
    a2 = win.get("a2") or {}
    return any(a2.get(src) or a2.get(src + "_median") is not None for src in RENDER_A2MAP)


def bundle_render_tier(fixtures: dict) -> str:
    """整束渲染数据档："A2"（每个渲染窗每序列哨兵全 ok）否则 "A"。"""
    ok_any = False
    for v in fixtures.values():
        for w in (v or {}).get("windows") or []:
            if not _has_render_seq(w):
                continue
            ok_any = True
            sent = w.get("sentinel") or {}
            for src in RENDER_A2MAP:
                a2 = w.get("a2") or {}
                present = bool(a2.get(src)) or a2.get(src + "_median") is not None
                if present and sent.get(src) != "ok":
                    return "A"
    return "A2" if ok_any else "A"
