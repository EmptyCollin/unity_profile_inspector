"""M7 总结报告（单文件 HTML，DESIGN §7.3 A 形态 / D8 已确认）。

包含 AGENTS §5 与 DESIGN §11 第 13 项要求的四块硬内容：
  ① §7.3 七张视图（算不出的**显式声明不计算并给原因**，不画假图）
  ② 前后对比全量指标表（热力图，劣化格标红，未评估盲区单列）
  ③ §8.7 过拟合体检表实际数值（G14 上线门禁的输入）
  ④ G20 不可检出清单（**与检出率同屏**，缺则报告无效）
外加证据链索引（每个数字可回溯到文件）与诚实边界声明。

四条渲染纪律（都是门禁要求，不是审美选择）：
- **小样本禁折线**：趋势样本 n<30 一律画散点（§7.3）。本轮判定轮次 n=2，
  所以所有"趋势"都只是两个点——图上必须写清这点，否则读者会把两点直线
  当成趋势。
- **口径同屏**：每个数字旁边给公式/阈值/hover 说明（§7.3"不允许只给数不给口径"）。
- **版本竖线**：detector/weights/pipeline 版本上线处打竖线。当前各版本全集
  只有一个值 ⇒ 无跳变可打，报告如实写明而不是省略这条规则。
- **只读**：报告不提供任何改阈值/改权限入口（§7.3 末条、I3）。

零第三方依赖：本机无 matplotlib/plotly/jinja2，图表手写 SVG。理由与 UI 一致——
可归档、可入仓库、离线可看，不引入装不上的东西。
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional

from upi.tooling import aggregate as AG
from upi.tooling import checklist as CL
from upi.tooling import undetectable as UD

ROOT = AG.ROOT
OUT = ROOT / "dashboard" / "报告.html"
# 留档根目录：每次调用现取 AG.RUNS，不在此固化（测试要能整体重定向）
MIN_N_LINE = 30                    # §7.3：n<30 禁折线


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def fmt(x, nd=2) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, (int,)):
        return f"{x:,}"
    if isinstance(x, float):
        if abs(x) >= 1e6:
            return f"{x/1e6:.2f}M"
        if abs(x) >= 1e3:
            return f"{x/1e3:.1f}k"
        return f"{x:.{nd}f}"
    return str(x)


# ─────────────────────────── SVG 原语 ───────────────────────────

def dots_svg(pts: list[tuple[float, float, str]], *, w=760, h=210,
             xmin=None, xmax=None, ymin=None, ymax=None,
             ylab="", xlab="", color="#2b6cb0", band: Optional[list] = None) -> str:
    """散点图（小样本唯一许可形态）。pts=[(x,y,label)]。band=置信区间带。"""
    if not pts:
        return '<div class="empty">无可用数据点</div>'
    pad = {"l": 62, "r": 18, "t": 14, "b": 30}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = min(xs) if xmin is None else xmin
    x1 = max(xs) if xmax is None else xmax
    y0 = min(ys + [0]) if ymin is None else ymin
    y1 = max(ys) if ymax is None else ymax
    if x1 == x0:
        x0, x1 = x0 - 0.5, x1 + 0.5
    if y1 == y0:
        y1 = y0 + (abs(y0) or 1) * 0.1

    def X(v): return pad["l"] + (v - x0) / (x1 - x0) * (w - pad["l"] - pad["r"])
    def Y(v): return h - pad["b"] - (v - y0) / (y1 - y0) * (h - pad["t"] - pad["b"])

    s = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    # 轴
    s.append(f'<line x1="{pad["l"]}" y1="{h-pad["b"]}" x2="{w-pad["r"]}" '
             f'y2="{h-pad["b"]}" stroke="#cbd5e0"/>')
    s.append(f'<line x1="{pad["l"]}" y1="{pad["t"]}" x2="{pad["l"]}" '
             f'y2="{h-pad["b"]}" stroke="#cbd5e0"/>')
    for frac in (0, 0.5, 1.0):
        v = y0 + (y1 - y0) * frac
        s.append(f'<text x="{pad["l"]-8}" y="{Y(v)+4}" text-anchor="end" '
                 f'class="tick">{esc(fmt(v))}</text>')
        s.append(f'<line x1="{pad["l"]}" y1="{Y(v)}" x2="{w-pad["r"]}" '
                 f'y2="{Y(v)}" stroke="#edf2f7"/>')
    if band and len(band) == 2:
        s.append(f'<rect x="{pad["l"]}" y="{Y(max(band)):g}" '
                 f'width="{w-pad["l"]-pad["r"]:g}" '
                 f'height="{abs(Y(min(band))-Y(max(band))):g}" '
                 f'fill="#48bb78" opacity="0.10"/>')
    for x, y, lab in pts:
        s.append(f'<circle cx="{X(x):g}" cy="{Y(y):g}" r="5" fill="{color}">'
                 f'<title>{esc(lab)}</title></circle>')
        s.append(f'<text x="{X(x):g}" y="{Y(y)-9:g}" text-anchor="middle" '
                 f'class="ptlbl">{esc(fmt(y))}</text>')
    s.append(f'<text x="{w/2}" y="{h-6}" text-anchor="middle" class="ax">'
             f'{esc(xlab)}</text>')
    s.append(f'<text x="12" y="{h/2}" class="ax" transform="rotate(-90 12 {h/2})" '
             f'text-anchor="middle">{esc(ylab)}</text>')
    s.append("</svg>")
    return "".join(s)


def bars_svg(rows: list[tuple[str, float, Optional[float]]], *, w=760, bh=22,
             color="#2b6cb0", unit="ms") -> str:
    """横向条形（阶段耗时 p50 + p95 幽灵条）。rows=[(label, main, secondary)]。"""
    if not rows:
        return '<div class="empty">无可用数据</div>'
    vmax = max([v for _, v, sv in rows for v in (v, sv or 0)] + [1e-9])
    lab_w = 150
    s = [f'<svg viewBox="0 0 {w} {len(rows)*bh+34}" class="chart">']
    for i, (lab, v, sv) in enumerate(rows):
        y = i * bh + 8
        full = (w - lab_w - 74)
        s.append(f'<text x="{lab_w-8}" y="{y+bh/2}" text-anchor="end" '
                 f'class="ax">{esc(lab)}</text>')
        if sv is not None and sv != v:
            s.append(f'<rect x="{lab_w}" y="{y+2}" width="{sv/vmax*full:g}" '
                     f'height="{bh-8}" fill="{color}" opacity="0.22">'
                     f'<title>p95 {esc(fmt(sv))} {esc(unit)}</title></rect>')
        s.append(f'<rect x="{lab_w}" y="{y+5}" width="{max(v/vmax*full,0.5):g}" '
                 f'height="{bh-12}" fill="{color}">'
                 f'<title>{esc(lab)}：{esc(fmt(v))} {esc(unit)}</title></rect>')
        s.append(f'<text x="{w-6}" y="{y+bh/2}" text-anchor="end" class="tick">'
                 f'{esc(fmt(v))}</text>')
    s.append(f'<text x="{lab_w}" y="{len(rows)*bh+26}" class="ax">'
             f'实心=p50，淡色=p95（单位 {esc(unit)}；口径见下方表格）</text>')
    s.append("</svg>")
    return "".join(s)


# ─────────────────────────── 数据装配 ───────────────────────────

def gather(db: Path | str | None = None) -> dict:
    # 只读连接统一由 aggregate._with_ro 开关：异常路径也必须释放，否则
    # Windows 上未关句柄把 stats.db 锁到进程退出——实测过后果是统计报错
    # 伪装成"文件删不掉"，第二个故障把第一个的现场一起毁掉。
    return AG._with_ro(db or AG.DB, lambda c: _gather_q(c, db))


def _gather_q(c, db: Path | str | None) -> dict:
    # ⚠️ kind='interaction'（守护命令 span 目录 daemon_cmdtrace）必须从
    #   报告的一切"轮次/阶段/事故/版本"轴里排除：它是**人机交互留痕**，
    #   G13 要求它进统计（有 span 就有据），但它不是判定轮次——
    #   混进来的后果（本轮实测）：视图1 成本散点多一个点、视图6 证据链
    #   冒出一个没有产物的"轮次"、pipeline 版本轴凭空多一种"pipeline"、
    #   事故表把 cmd:cancel 的 rc≠0 当成采集事故。交互动作改由
    #   meta.cost.command_actions 单列。
    runs = c.execute("""SELECT run_id, kind, fixture, started_at, ended_at,
                        wallclock_s, tokens, route, verdict, attempts, traced,
                        findings, unevaluated_guards, protocol_fingerprint,
                        time_source, env_fingerprint
                        FROM runs WHERE kind <> 'interaction'
                        ORDER BY ended_at, run_id""").fetchall()
    guards = c.execute("""SELECT g.run_id, ru.fixture, g.metric, g.before, g.after,
                          g.delta, g.regressed, g.value_source, g.basis
                          FROM guards g JOIN runs ru ON ru.run_id=g.run_id
                          ORDER BY g.run_id, g.metric""").fetchall()
    unev = c.execute("""SELECT u.run_id, ru.fixture, u.metric, u.value_source,
                        u.basis, u.window_before, u.window_after, u.window_delta
                        FROM unevaluated u JOIN runs ru ON ru.run_id=u.run_id
                        ORDER BY u.run_id, u.metric""").fetchall()
    stages = c.execute("""SELECT stage, SUM(n), SUM(n_ok), AVG(p50_ms), AVG(p95_ms),
                          MAX(max_ms), SUM(total_ms), SUM(tokens)
                          FROM stage_stats GROUP BY stage ORDER BY SUM(total_ms) DESC""").fetchall()
    # 事故与工具调用：同样只取判定轮次的 span。命令 span 的 exit_status≠0
    # 表示"这条命令被拒/未知"，与"采集出事故"是两回事，混进视图 3 会把
    # 用户按错键画成仪器故障（判据必须按来源分，不能只看非零码）。
    incidents = c.execute("""SELECT s.run_id, s.stage, s.exit_status, COUNT(*)
                             FROM spans s JOIN runs ru ON ru.run_id = s.run_id
                             WHERE s.exit_status <> 0 AND ru.kind <> 'interaction'
                             GROUP BY 1, 2, 3""").fetchall()
    tools = c.execute("""SELECT s.tool_kind, s.tool_name, COUNT(*), AVG(s.tool_ms)
                         FROM spans s JOIN runs ru ON ru.run_id = s.run_id
                         WHERE s.tool_name IS NOT NULL AND ru.kind <> 'interaction'
                         GROUP BY 1, 2 ORDER BY 3 DESC""").fetchall()
    conflicts = c.execute("""SELECT run_id, fixture, conflict_rules FROM detections
                             WHERE conflict_rules NOT IN ('[]','') """).fetchall()
    versions = {"detector": [r[0] for r in c.execute(
        "SELECT DISTINCT detector_version FROM detections WHERE detector_version IS NOT NULL")],
        # 版本轴同样排除 interaction：否则 pipeline 会凭空多出一种
        # "版本取值"，而视图头部的"均无跳变"判定就变成假的。
        "pipeline": sorted({r[0].split("_")[0] for r in c.execute(
            "SELECT DISTINCT kind FROM runs WHERE kind <> 'interaction'")})}
    # weights_version 的真实来源是 state.json 的 hypotheses[]（每项带该字段）。
    # 曾按 diagnose_archive.json 的 "hypotheses" 键取，但归档实况是
    # {根因描述: {六因子判定}} 的字典，无该键 ⇒ 恒取不到值。
    # "假空"比"真空"危险：报告会说"无版本变更"，实际是解析没到位。
    weights = []
    n_diag_runs = n_hypotheses = 0
    # 审查/回滚记录：从各轮 state.json 的 records 取（实况：rollback 嵌在 review 记录里）
    reviews, rollbacks = [], []
    for p in sorted(AG.RUNS.glob("*/state.json")):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rid = p.parent.name
        hyps = st.get("hypotheses") or []
        if hyps:
            n_diag_runs += 1
            n_hypotheses += len(hyps)
            for h in hyps:
                wv = (h or {}).get("weights_version")
                if wv and wv not in weights:
                    weights.append(wv)
        for r in (st.get("records") or []):
            if not isinstance(r, dict):
                continue
            if r.get("kind") == "review":
                reviews.append({"run_id": rid, "round": r.get("round"),
                                "verdict_status": r.get("verdict_status"),
                                "rollback": bool(r.get("rollback"))})
            if r.get("rollback") or r.get("kind") == "rollback":
                rb = r.get("rollback") or r
                rollbacks.append({"run_id": rid, "to": rb.get("rolled_back_to"),
                                  "head": (rb.get("head_after") or "")[:8],
                                  "asset_restored": rb.get("asset_manifest_restored"),
                                  "scene_restored": rb.get("scene_restored")})
        try:
            rep = json.loads((p.parent / "runreport.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rep = {}
        fr = rep.get("final_rollback")
        if fr:
            rollbacks.append({"run_id": rid, "to": fr.get("rolled_back_to"),
                              "head": (fr.get("head_after") or "")[:8],
                              "asset_restored": fr.get("asset_manifest_restored"),
                              "scene_restored": fr.get("scene_restored"),
                              "出口兜底": True})
    versions["weights"] = weights
    return {"runs": runs, "guards": guards, "unevaluated": unev, "stages": stages,
            "incidents": incidents, "tools": tools, "conflicts": conflicts,
            "versions": versions, "reviews": reviews, "rollbacks": rollbacks,
            # 校准视图的样本量：不硬编码进文案，随留档实况变（§7.3 口径同屏）
            "diag_scale": {"n_runs_with_hypotheses": n_diag_runs,
                           "n_hypotheses": n_hypotheses},
            "meta": AG.meta_metrics(db or AG.DB),
            "checklist": CL.compute(db or AG.DB),
            "blind": UD.build(db or AG.DB)}
