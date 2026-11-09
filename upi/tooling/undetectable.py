"""G20 不可检出清单（报告强制章节）。

AGENTS G20：每份报告必须含此清单，**与检出率同屏**，逐项注明受阻原因与解锁
路径；缺该清单的报告无效。§5 又要求"诚实边界显式声明"——两者是同一件事的
两面：检不出什么，和测的是什么环境，必须一起说。

清单条目不硬编码"当前有几项"：每条给出**受阻根因**（哪条门禁/哪个环节拦住了
它）与**解锁路径**（哪个里程碑做完就能出数），并挂当轮实测证据——证据从
SQLite 现算，不是抄文档。这样做的代价是多几行取数代码，收益是清单里的每一项
都能被证伪；如果某天 detector 注册了 GPU 逐笔归因，第 1 项会自动变成"已解锁"，
不会留在报告里当装饰。
"""
from __future__ import annotations

import json
from pathlib import Path

from upi.tooling import aggregate as AG

# 记忆体盲区口径的唯一来源（与判定层同一个表，避免报告与代码两处漂移）
from upi.core.memory_guard import FRESH_POST_GC_METRICS, GC_UNRESPONSIVE_KNOWN


def build(db: Path | str | None = None, runs_root: Path | None = None) -> dict:
    c = AG.connect_ro(db or AG.DB)
    # 缺省每次调用现取 AG.RUNS（而非模块级快照），便于测试整体隔离
    runs = runs_root or AG.RUNS

    # ── 1. GPU 尖峰的逐笔归因 ──
    # 受阻根因：G19——判"发作"必须有逐帧源，而渲染计数器无任何可用逐帧源
    #（实测 A2 静默读 0、A3 为 -1 哨兵、Frame Debugger 程序侧取不到可解读明细）
    # ⇒ 禁止注册 GPU 逐笔 spike detector。
    gpu_detectors = c.execute(
        "SELECT DISTINCT detector FROM detections WHERE detector LIKE '%GPU%'").fetchall()
    spike_registered = any("SPIKE" in (d[0] or "").upper() for d in gpu_detectors)
    item1 = {
        "id": "gpu_spike_attribution",
        "title": "GPU 尖峰的逐笔归因（哪一笔 draw call 造成这一帧越线）",
        "blocked_by": "G19 逐帧源门禁：渲染计数器无可用逐帧源"
                      "（A2 静默读 0 / A3 -1 哨兵 / FD 程序侧不可解读，DESIGN §12 实测 ①-⑫）",
        "unlock": "EX-1（切 DX11 验证 RenderDoc 程序化解析）或 M8 B 档真机采样；"
                  "期间 GPU 发作 detector 只能建在 A4 gpuFrameTime 上，"
                  "且 finding 只给越线事实、attribution_source=human",
        "state": "unlocked" if spike_registered else "blocked",
        "evidence": {"gpu_detector_registered": [d[0] for d in gpu_detectors],
                     "spike_detector_present": spike_registered},
    }

    # ── 2. 未放大的代码风险（P3 potential）──
    pot_files = list((runs).glob("*/potentials")) if runs.exists() else []
    pot_records = []
    for d in pot_files:
        for f in sorted(d.glob("*.json")):
            pot_records.append(f.name)
    item2 = {
        "id": "unamplified_code_risk",
        "title": "未放大的代码风险（热路径 new List、材质实例膨胀等——当前规模零痕迹）",
        "blocked_by": "M3.5 C+ 静态扫描 + 斜率投影未建：P3 的证据在代码里不在运行时数据里，"
                      "A 档采样原理上抓不到（DP-1 §1.1 仪器选型）",
        "unlock": "M3.5：代码模式扫描器（C+）+ 斜率投影器 + 压测协议（10×/100×，D15）；"
                  "上线前 T5 压测复现是 potential→confirmed 的唯一路径（G18）",
        "state": "blocked",
        "evidence": {"potential_dirs_found": len(pot_files),
                     "potential_records": pot_records[:5]},
    }

    # ── 3. 感知类退化（P5）──
    # 从各轮 state.json 的 verdict.perception 看：字段存在但门禁未真正跑起来
    perc_checked = perc_missing = 0
    perc_shapes: list[str] = []
    for p in sorted(runs.glob("*/state.json")):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pc = (st.get("verdict") or {}).get("perception")
        if pc is None:
            continue
        if isinstance(pc, dict):
            if pc.get("passed") is None or not any(
                    v is not None for k, v in pc.items() if k != "passed"):
                perc_missing += 1
                perc_shapes.append(f"{p.parent.name}: 字段空壳")
            else:
                perc_checked += 1
    item3 = {
        "id": "perception_regression",
        "title": "感知类退化（画面 SSIM / 动画事件序列 / 固定种子回放发散）",
        "blocked_by": "M7 T8 感知门禁基建未落地：无截图 SSIM、无动画事件 diff、"
                      "无种子回放 ⇒ G3 目前只在纸面（AGENTS §1.5 感知门禁失败即回滚）",
        "unlock": "M7 T8：种子回放 + 截图 SSIM + 动画事件 diff；"
                  "凡 EditPlan 触碰 §3 维度一列表即强制 semi（先报后批），"
                  "自动判定不依赖 LLM 主观（I5）",
        "state": "partially_blocked",
        "evidence": {"perception_实算轮次": perc_checked,
                     "perception_空壳轮次": perc_missing,
                     "说明": "空壳=字段存在但数值未测，不得当作已通过"},
    }

    # ── 4. 记忆体守护覆盖面 ──
    # 唯一一项"有实测数据支撑"的盲区：直接取 G20 灌进 SQLite 的未评估表
    unev = c.execute("SELECT metric, COUNT(*) FROM unevaluated GROUP BY metric").fetchall()
    unev_map = dict(unev)
    item4 = {
        "id": "memory_guard_coverage",
        "title": "记忆体守护覆盖面：修复是否多吃内存，当前只有 monoUsedBytes 一个口径能答",
        "blocked_by": "实测 monoHeapBytes / totalAllocatedBytes / totalReservedBytes / "
                      "total_used_per_frame 强制 GC 后 Δ≈0（不受回收影响）——"
                      "它们是会话累积量而非净占用，跨会话不可比；"
                      "窗口值量的是'此刻未回收多少'，回收时点一变即失真",
        "unlock": "M8 B 档真机采样，或更长隔离的 fresh post-GC 采集（两侧各自 fresh，"
                  "任一侧缺即记未评估盲区，既不判劣化也不判清白，§1.5）",
        "state": "blocked" if unev else "evaluated",
        "evidence": {"本轮/历史实测未评估次数": unev_map,
                     "受保护记忆体指标全集": sorted(FRESH_POST_GC_METRICS),
                     "已知不响应 GC 者": sorted(GC_UNRESPONSIVE_KNOWN)},
    }

    items = [item1, item2, item3, item4]
    # 与检出率同屏（G20 的字面要求）：清单和检出率一起返回，报告层不得拆分渲染
    rp = AG._recall_pairs(db or AG.DB)
    c.close()
    return {"items": items,
            "undetectable": True,
            "n_blocked": sum(1 for i in items if i["state"] != "unlocked"),
            "paired_with_detection_rate": rp,
            "note": "G20：缺本清单的报告无效；本清单与检出率必须同屏展示"}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
