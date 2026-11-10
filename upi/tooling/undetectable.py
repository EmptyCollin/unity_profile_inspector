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
    # 只读连接统一由 aggregate._with_ro 开关（异常路径也释放，同 gather）
    return AG._with_ro(db or AG.DB, lambda c: _build_q(c, db, runs_root))


def _build_q(c, db: Path | str | None, runs_root: Path | None) -> dict:
    # 缺省每次调用现取 AG.RUNS（而非模块级快照），便于测试整体隔离
    runs = runs_root or AG.RUNS

    # ── 1. GPU 尖峰的逐笔归因 ──
    # 2026-11-11 #29 改判（exp5/exp6 + counter_cat_full.txt 5298 行句柄全量表）：
    # 旧文本"渲染计数器无任何可用逐帧源 ⇒ 禁止注册逐笔 spike detector"的根因是
    # 句柄名错（真名带 Count 后缀）+ UI Toolkit 同名遮蔽——双条件定位修复后注入侧
    # 与轮询侧比值 1.00（G16 新文）。本项范围随之收窄为 **GPU 侧逐笔归因**：
    # GPU Frame Time 是方法级采样计数器（非逐帧 GPU 时间线，A4 才是发作口径），
    # 且 FD 程序侧不可解读未被推翻 ⇒ 归因仍转人工。
    # spike_registered 判据同步改语义：渲染计数 detector 在场是**解禁后的应有状态**
    # （≠归因解锁）；GPU 归因解锁信号= FD 可解读明细或 B 档真机通路，当前无源
    # ⇒ 恒 blocked（旧判据"注册即 unlocked"是"虚报检出能力"入口，按 I1 关掉）。
    gpu_detectors = c.execute(
        "SELECT DISTINCT detector FROM detections WHERE detector LIKE '%GPU%'").fetchall()
    gpu_attribution_source = None  # 自动归因源探针：FD 程序化明细（EX-1）或 B 档（M8）
    item1 = {
        "id": "gpu_spike_attribution",
        "title": "GPU 尖峰的逐笔归因（哪一笔 draw call 造成这一帧越线）",
        "blocked_by": "GPU 侧无可解读逐笔源：FD 程序侧不可解读（DESIGN §12 实测 ①-⑫，"
                      "未被 exp5 推翻）；GPU Frame Time 计数器是方法级采样，"
                      "非逐帧 GPU 时间线 ⇒ 发作口径仍只 A4 gpuFrameTime。"
                      "注意：渲染**计数**的逐帧 spike detector 已随 G16/G19 改判解禁"
                      "（exp5/exp6 比值 1.00 实证），本项只覆盖'归因到哪一笔'。",
        "unlock": "EX-1（切 DX11 验证 RenderDoc 程序化解析）或 M8 B 档真机采样；"
                  "期间 GPU 发作 detector 建在 A4 gpuFrameTime，"
                  "finding 只给越线事实、attribution_source=human",
        "state": "unlocked" if gpu_attribution_source else "blocked",
        "evidence": {"gpu_detector_seen": [d[0] for d in gpu_detectors],
                     "自动归因源在场": bool(gpu_attribution_source),
                     "说明": "detector 注册不再作为解锁信号（旧判据会把检出能力"
                             "冒充归因能力）；解锁只认真实归因源"},
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

    # ── 5. batches 汇总口径无注入通路（#27 exp5/exp6/exp7 实测钉出）──
    # 句柄全量表证实 Render 类目**没有**汇总 "Batches Count"，只有 Dynamic/
    # Instanced/Static 三分项 ⇒ 注入侧拿不到与轮询 render.batches 同口径的数；
    # 两侧同零的 SRP 场景里等价性无从对表（零对零无信息量，exp7 翻转裁决无效：
    # QualitySettings.enableDynamicBatching 在 Unity 6000 不存在，CS0117 实证）。
    # 证据现算：本机句柄表在场就数给它看，不在场就如实标未验证（非抄文档）。
    cat_fp = runs / "_probe" / "counter_cat_full.txt"
    if cat_fp.exists():
        try:
            _lines = cat_fp.read_text(encoding="utf-8").splitlines()
        except OSError:
            _lines = []
        _agg = [x for x in _lines if x == "Render|Batches Count"]
        _sub = [x for x in _lines if x.startswith("Render|")
                and x.endswith("Batches Count") and x != "Render|Batches Count"]
        b5_ev = {"句柄表行数": len(_lines),
                 "Render|Batches Count(汇总)": _agg or "不存在",
                 "Render 分项合批句柄": _sub,
                 "draw_calls_遮蔽样本在场": any(
                     x.startswith("UI Toolkit|Draw Calls Count") for x in _lines)}
        b5_state = "blocked" if not _agg else "unlocked"
    else:
        b5_ev = {"句柄表": "本机 runs/_probe/counter_cat_full.txt 未在场",
                 "说明": "本项为结构性结论（DESIGN §12 / exp5~7 落文）；"
                         "隔离环境无句柄表 ⇒ 如实标未验证，不冒充实测"}
        b5_state = "blocked"
    item5 = {
        "id": "batches_injection_parity",
        "title": "batches 合批数：注入侧无汇总句柄（仅三分项），逐帧注入与轮询等价性对不了表",
        "blocked_by": "Render 类目结构性无汇总 Batches Count 句柄；SRP 常态场景"
                      "两侧同零（零对零无信息量）；dynamic batching 翻转路线在 "
                      "Unity 6000 无对应 API（CS0117 实证，exp7 作废）",
        "unlock": "真实非零合批场景（GPU instancing 同材质多实例 / 静态合批 / "
                  "内置管线对照）上做两侧对表；或接受 batches 仅轮询单源并标注",
        "state": b5_state,
        "evidence": b5_ev,
    }

    items = [item1, item2, item3, item4, item5]
    # 与检出率同屏（G20 的字面要求）：清单和检出率一起返回，报告层不得拆分渲染
    rp = AG._recall_pairs(db or AG.DB)
    return {"items": items,
            "undetectable": True,
            "n_blocked": sum(1 for i in items if i["state"] != "unlocked"),
            "paired_with_detection_rate": rp,
            "note": "G20：缺本清单的报告无效；本清单与检出率必须同屏展示"}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=1))
