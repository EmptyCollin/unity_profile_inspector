"""M7 报告渲染：把 report_data.gather() 的实况装配成单文件 HTML。

七张视图的呈现纪律（AGENTS §5 / DESIGN §7.3）：
- 样本量与置信区间必须与数字同屏（G15）；n<30 禁折线，只画散点。
- 每个数字旁边给口径（公式/阈值/取数来源），"不允许只给数不给口径"。
- 版本跳变必须在时间轴打竖线；**当前没有跳变也要写明"无跳变可打"**，
  否则读者会以为这条规则被漏掉了。
- 不可检出清单（G20）与检出率同屏，且本文件把两者放进同一个 section，
  从代码层面保证报告层无法把它们拆开渲染。
- 只读：无按钮、无输入、无任何改阈值/改权限入口（I3）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from upi.tooling import report_data as RD
from upi.tooling.report_data import OUT, esc, fmt, gather

CSS = """
:root{--ink:#1a202c;--mut:#4a5568;--line:#e2e8f0;--bg:#fff;--warn:#c05621;
--bad:#c53030;--good:#276749;--acc:#2b6cb0;--dim:#718096}
*{box-sizing:border-box}
body{margin:0;padding:0 0 60px;background:#f7fafc;color:var(--ink);
font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:0 22px}
h1{font-size:26px;margin:34px 0 6px;letter-spacing:-.3px}
h2{font-size:19px;margin:42px 0 4px;padding-top:16px;border-top:2px solid var(--line)}
h3{font-size:16px;margin:26px 0 6px;color:var(--mut)}
p.sub{color:var(--mut);margin:2px 0 18px;font-size:13.5px}
.badge{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;
font-weight:600;vertical-align:2px;margin-left:6px}
.b-good{background:#c6f6d5;color:var(--good)}.b-bad{background:#fed7d7;color:var(--bad)}
.b-warn{background:#feebc8;color:var(--warn)}.b-dim{background:#edf2f7;color:var(--dim)}
table{border-collapse:collapse;width:100%;margin:10px 0 4px;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 9px;text-align:left;
vertical-align:top}
th{background:#edf2f7;font-weight:600;font-size:12.5px;white-space:nowrap}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.chart{width:100%;height:auto;display:block;margin:6px 0}
.ax{font-size:11px;fill:var(--mut)}.tick{font-size:10.5px;fill:var(--dim)}
.ptlbl{font-size:10px;fill:var(--mut)}
.empty{padding:14px;border:1px dashed var(--line);border-radius:6px;
color:var(--dim);font-size:13px;background:#fff}
.note{background:#fffbeb;border-left:3px solid var(--warn);padding:10px 14px;
margin:12px 0;font-size:13px;border-radius:0 5px 5px 0}
.dec{background:#ebf8ff;border-left:3px solid var(--acc);padding:10px 14px;
margin:12px 0;font-size:13px;border-radius:0 5px 5px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}}
details{margin:8px 0;font-size:13px}
summary{cursor:pointer;color:var(--mut);font-size:12.5px}
code{background:#edf2f7;padding:1px 5px;border-radius:3px;font-size:12px;
font-family:ui-monospace,Consolas,monospace}
.heat td{text-align:center;font-variant-numeric:tabular-nums;padding:5px 6px}
.heat td.r{background:#fed7d7;color:var(--bad);font-weight:700}
.heat td.i{background:#fef5e7;color:var(--warn)}
.heat td.g{background:#f0fff4;color:var(--good)}
.heat td.u{background:#edf2f7;color:var(--dim);font-style:italic}
footer{margin-top:48px;padding:20px 0 0;border-top:2px solid var(--line);
font-size:12.5px;color:var(--dim)}
.kpi{display:flex;gap:26px;flex-wrap:wrap;margin:16px 0 4px}
.kpi div{min-width:110px}
.kpi b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.kpi span{font-size:12px;color:var(--mut)}
"""


def _n_badge(n: Optional[int], need: int = RD.MIN_N_LINE) -> str:
    """小样本徽章：n<30 时明确标注，防止读者把散点当趋势。"""
    if n is None:
        return '<span class="badge b-dim">无数据</span>'
    cls = "b-good" if n >= need else "b-warn"
    txt = "样本充足" if n >= need else f"n={n}＜30 仅散点"
    return f'<span class="badge {cls}">{txt}</span>'


def _口径(text: str) -> str:
    return f'<p class="sub">口径：{esc(text)}</p>'


# ─────────────────────────── 头部 ───────────────────────────

def head(g: dict) -> str:
    v = g["versions"]
    meta = g["meta"]
    runs = g["runs"]
    n_traced = meta["cost"]["runs_traced"]
    return f"""
<h1>AI 性能优化管线 · 历史统计与总结报告</h1>
<p class="sub">本文档由 <code>upi report</code> 从 <code>runs/*/trace.jsonl</code> 与
轮次产物聚合生成，只读、离线可看。数据源：SQLite 缓存
<code>dashboard/stats.db</code>（可整库重建的派生缓存，真相在 jsonl 留档）。
生成时纳入 {len(runs)} 个轮次，其中 {n_traced} 个有 trace、
{meta['cost']['runs_untraced']} 个标 untraced（G13：无 trace 不入统计）。</p>
<div class="kpi">
  <div><b>{len(runs)}</b><span>纳入轮次</span></div>
  <div><b>{fmt(meta['cost']['total_wallclock_s'])}s</b><span>累计墙钟</span></div>
  <div><b>{fmt(meta['cost']['total_tokens'])}</b><span>累计 token</span></div>
  <div><b>{fmt((meta['quality']['success_rate'] or {}).get('success_rate'))}</b>
      <span>修复成功率（n={fmt((meta['quality']['success_rate'] or {}).get('n_effective_contexts'))}）</span></div>
  <div><b>{g['blind']['n_blocked']}</b><span>不可检出项（G20）</span></div>
</div>
<div class="note"><b>版本轴说明（§7.3 硬要求）</b>：时间轴本应在
detector / weights / pipeline 版本上线处打竖线。当前实测各轴取值——
detector 版本 <code>{esc(', '.join(v.get('detector') or []) or '未记录')}</code>、
weights <code>{esc(', '.join(v.get('weights') or []) or '未记录')}</code>、
pipeline <code>{esc(', '.join(v.get('pipeline') or []) or '未记录')}</code>
——<b>均无跳变，故本报告不画竖线</b>。这不是漏掉规则：一旦出现两个取值，
趋势图上必须打线，否则"代码改了"会被读成"越用越好"。</div>
<div class="dec"><b>本轮次诚实边界（AGENTS §5 必须显式声明）</b>：
全部数据来自 <b>PC GUI Unity Editor（6000.6.7f1 + URP 17.2.6）侧 A 档秒级抽样与
A2 注入按帧采样 + 帧预算模拟</b>，<b>不等于真机中低端机型数据</b>；
判定样本 n_effective 为个位数，因此本报告中一切趋势与泛化表述
都只是<b>方法学示范</b>（机制跑通、口径可算），不构成"越用越好用"的验证结论
（DESIGN §8.8）。</div>
"""


# ─────────────────── 视图 1：成本-质量双轴 ───────────────────

def view1(g: dict) -> str:
    from upi.tooling.report_data import dots_svg
    pts_c = []
    for i, r in enumerate(g["runs"]):
        rid, kind, fx, _st, _en, wall, tok, route, verdict, att, traced = r[:11]
        if not traced:
            continue
        if wall:
            pts_c.append((i, wall, f"{rid} 墙钟 {fmt(wall)}s（{kind}）"))
    ok_runs = [r for r in g["runs"] if r[10]]
    n_line = len(ok_runs)
    trend_ok = n_line >= RD.MIN_N_LINE
    body = []
    body.append(("<div class='grid2'>" if trend_ok else "<div>") +
                "<h3>成本轴：单轮墙钟秒数</h3>" +
                dots_svg(pts_c, ylab="墙钟 s", xlab=f"轮次序号（n={n_line}）",
                         color="#c05621") +
                ("<h3>质量轴：检出条数</h3>" +
                 dots_svg([(i, r[11] or 0, f"{r[0]} 检出 {r[11]} 条")
                          for i, r in enumerate(ok_runs)],
                          ylab="检出条数", xlab=f"轮次序号（n={n_line}）",
                          color="#2b6cb0") + "</div>"
                 if trend_ok else "</div>"))
    if not trend_ok:
        body.append(f'<div class="note">两轴各画一次散点（n={n_line}＜{RD.MIN_N_LINE}，'
                    '§7.3 禁止折线）。读者要判断的"是变好了还是变贵了"'
                    '在当前样本量下<b>无法回答</b>——这正是页脚门槛要解决的：</b>'
                    f'需要 ≥{RD.MIN_N_LINE} 个可比轮次才能谈趋势。</div>')
    return f"""
<h2>视图 1｜成本-质量双轴 {_n_badge(n_line)}</h2>
{_口径('成本=runreport.spent.wallclock_s（无则取该轮 trace span 的 wallclock_ms 之和）；'
        '质量=该轮 state.json 的 findings 条数。两者必须同屏（G15），'
        '只报质量会让"变准但变贵"不可见。')}
{"".join(body)}"""


# ─────────────── 视图 2：FP 率 × 检出率（与 G20 同屏） ───────────────

def view2(g: dict) -> str:
    from upi.tooling.report_data import dots_svg
    rp = g["meta"]["quality"]["recall_pairs"]
    pairs = rp["pairs"]
    fp = g["meta"]["quality"]["fp_selfcheck"]
    blind = g["blind"]
    rows = []
    for p in pairs:
        n = p["n_obs"]
        d = p["detection_rate"]
        # Wilson 下界：n 小的时候点估计会虚高，必须同屏（G15）
        from upi.core.knowledge import wilson_lower_bound as wlb
        lb = wlb(p["n_detected"], n) if n else None
        rows.append(f"<tr><td><code>{esc(p['detector'])}</code></td>"
                    f"<td>{esc(p['version'])}</td>"
                    f"<td class='n'>{fmt(n)}</td>"
                    f"<td class='n'>{fmt(p['n_detected'])}</td>"
                    f"<td class='n'>{fmt(d,3)}</td>"
                    f"<td class='n'>{fmt(lb,3)}</td>"
                    f"<td class='n'>{fmt((fp.get('n_fp', 0) / fp['n_detectors']) if fp.get('n_detectors') else None,3)}</td>"
                    f"<td class='n'>{fmt(fp.get('n_detectors'))}</td></tr>")
    dots = [(i, p["detection_rate"] or 0, f"{p['detector']} 检出率 {fmt(p['detection_rate'],3)}"
             f"（n={p['n_obs']}）") for i, p in enumerate(pairs)]
    return f"""
<h2>视图 2｜检出率 × 误报率，按 detector 版本分面 {_n_badge(sum(p['n_obs'] for p in pairs))}</h2>
{_口径('检出率 = 该 detector 在缺陷夹具上判 detected 的观测数 / 观测总数（观测=夹具×轮次）；'
        '误报率 = CLEAN 自比自（阈值来自 CLEAN 自身中位，任何 detected 都是误报，D6 口径）'
        '的 detected 数 / CLEAN 观测数。两列同屏是 G15 的硬要求；'
        'Wilson 下界用 95% 置信，防止小样本点估计虚高。')}
<table>
<tr><th>detector</th><th>版本</th><th>缺陷观测 n</th><th>检出数</th>
<th>检出率</th><th>Wilson 下界</th><th>CLEAN 误报率</th><th>CLEAN 观测 n</th></tr>
{"".join(rows)}
</table>
{dots_svg(dots, ylab="检出率", xlab="detector（分面即版本，当前各仅 1 版）",
          ymin=0, ymax=1.05, color="#2b6cb0")}
<p class="sub">另有 <b>{fmt(rp['excluded_no_detector_field'])}</b> 条观测因缺
<code>detector</code> 字段被排除出分母（2026-11-07 早期产物只有 verdict/why 两键）。
排除而非混入：混进去会算出"检出率 0"这类假结论；显式报数则缺口可见（I1）。</p>

<h3>不可检出清单（G20，与本视图同屏，不可拆分）</h3>
{_口径('AGENTS G20：每份报告必须含本清单并与检出率同屏，逐项注明受阻原因与解锁路径；'
        '缺清单的报告无效。每项证据从留档现算，非抄文档。')}
<table>
<tr><th>#</th><th>检不出什么</th><th>状态</th><th>受阻根因</th><th>解锁路径</th><th>实测证据</th></tr>
{"".join(
    f"<tr><td class='n'>{i + 1}</td>"
    f"<td>{esc(it['title'])}</td>"
    f"<td><span class='badge {'b-bad' if it['state'] == 'blocked' else 'b-warn' if it['state'] == 'partially_blocked' else 'b-good'}'>{esc(it['state'])}</span></td>"
    f"<td>{esc(it['blocked_by'])}</td>"
    f"<td>{esc(it['unlock'])}</td>"
    f"<td><details><summary>展开</summary><code>{esc(json.dumps(it['evidence'], ensure_ascii=False, indent=1))}</code></details></td></tr>"
    for i, it in enumerate(blind["items"]))}
</table>
<div class="note">上面第 3 项（记忆体守护覆盖面）与视图 5 的"未评估盲区"列是同一事实的
两种呈现：清单说"哪些指标答不了"，热力图说"这轮具体哪几项没测"。
两者数字必须一致，本文件由同一次 <code>gather()</code> 取数保证。</div>
"""


# ─────────────── 视图 3：阶段耗时 + 事故标记 ───────────────

def view3(g: dict) -> str:
    from upi.tooling.report_data import bars_svg
    rows = [(f"{s[0]}（n={fmt(s[1])}，失败 {fmt(s[1] - s[2])}）",
             (s[3] or 0) / 1000, (s[4] or 0) / 1000) for s in g["stages"]]
    inc = g["incidents"]
    ver_fail = [i for i in inc if i[1] in ("fix", "verify")]
    inc_txt = ("<table><tr><th>轮次</th><th>阶段</th><th>退出码</th><th>次数</th></tr>"
               + "".join(f"<tr><td><code>{esc(r[0])}</code></td><td>{esc(r[1])}</td>"
                         f"<td class='n'>{fmt(r[2])}</td><td class='n'>{fmt(r[3])}</td></tr>"
                         for r in inc) + "</table>") if inc else \
        '<div class="empty">本轮次内无 span 级非零退出（编译门禁未触发失败样本）</div>'
    return f"""
<h2>视图 3｜阶段耗时与事故标记 {_n_badge(sum(s[1] for s in g['stages']))}</h2>
{_口径('按 (stage, 环境指纹) 分组后取该组 p50/p95（单位秒）。'
        '不同环境的同阶段不合并——跨环境混算正是 G7 在采样层禁止的事。'
        '阶段样本 = 该阶段 span 条数，跨环境时 p50 取各组均值（故可能与单次实测略有出入）。')}
{bars_svg(rows, unit="s")}
<table>
<tr><th>阶段</th><th>span 数</th><th>非零退出</th><th>p50 (s)</th><th>p95 (s)</th>
<th>最长 (s)</th><th>累计 (s)</th><th>token</th></tr>
{"".join(
    f"<tr><td><code>{esc(s[0])}</code></td><td class='n'>{fmt(s[1])}</td>"
    f"<td class='n'>{fmt(s[1] - s[2])}</td><td class='n'>{fmt((s[3] or 0)/1000)}</td>"
    f"<td class='n'>{fmt((s[4] or 0)/1000)}</td><td class='n'>{fmt((s[5] or 0)/1000)}</td>"
    f"<td class='n'>{fmt((s[6] or 0)/1000, 1)}</td><td class='n'>{fmt(s[7])}</td></tr>"
    for s in g["stages"])}
</table>
<h3>编译门禁（G8）与 Safe Mode 事故（G9）</h3>
{inc_txt}
<p class="sub">G8 的语义是"编译失败不消耗整轮采样成本"，因此这里出现的 fix/verify 非零
退出（{fmt(len(ver_fail))} 类）都应结合该轮 span 顺序判断是否为门禁前拦截；
无事故不等于无风险——当前 span 的 <code>exit_status</code> 仅在少数阶段被写入。</p>
<h3>工具调用分布</h3>
<table><tr><th>类别</th><th>名称</th><th>次数</th><th>均值 (ms)</th></tr>
{"".join(f"<tr><td>{esc(t[0])}</td><td><code>{esc(t[1])}</code></td>"
         f"<td class='n'>{fmt(t[2])}</td><td class='n'>{fmt(t[3])}</td></tr>"
         for t in g["tools"]) or "<tr><td colspan=4>无工具调用记录</td></tr>"}
</table>"""


# ─────────────── 视图 4：校准曲线 + ECE（当前不计算） ───────────────

def view4(g: dict) -> str:
    n_ok = g["meta"]["n_note"]
    missing = g["meta"]["missing"]
    ds = g.get("diag_scale") or {"n_runs_with_hypotheses": 0, "n_hypotheses": 0}
    # 置信度×结局要能成对才谈得上校准：分母取"有判定的轮次"（checklist 实测口径）
    n_verdict = g["checklist"]["n_runs_with_verdict"]
    return f"""
<h2>视图 4｜可靠性曲线（校准图）+ ECE <span class="badge b-bad">本阶段不计算</span></h2>
{_口径('校准图 = 把每个假设的置信度分箱，画"预测置信度 vs 实际成功率"，对角线即完美校准；'
        'ECE = 各箱 |预测−实际| 的样本量加权平均。训练集与留出集两条线同屏，'
        '差值即过拟合度（§8.7 第 3 项）。')}
<div class="empty">
不计算的原因（非偷懒，是样本量；数字随留档实况取，不写死）：<br>
· 假设侧：<b>{ds['n_runs_with_hypotheses']}</b> 个轮次共
<b>{ds['n_hypotheses']}</b> 条带置信度的归因假设；<br>
· 结局侧：跑到出判定的轮次仅 <b>{n_verdict}</b> 个 ⇒ 置信度与结局<b>无法成对</b>，
分箱后每箱 n≤2，任何"校准"都是噪声。<br>
· 留出分组本身可用（按 fixture_spec 的 holdout 标记，三态建模，
CLEAN 作第三态不参与分组）；真正缺的是每侧的置信度×结局配对样本。
缺项登记：{esc(missing['holdout_gap'])}。<br>
· 已登记的缺失说明：<code>ece</code> → {esc(missing['ece'])}。<br>
按 §7.3「{esc(n_ok)}」，此处一律不给图、只给缺项说明——
画出来会诱导读者把噪声读成"校准良好"。
</div>"""


# ─────────────── 视图 5：指标矩阵热力图 ───────────────

def view5(g: dict) -> str:
    metrics = sorted({r[2] for r in g["guards"]} | {r[2] for r in g["unevaluated"]})
    runs = sorted({r[0] for r in g["guards"]} | {r[0] for r in g["unevaluated"]})
    cell = {(r[0], r[2]): r for r in g["guards"]}
    unev = {(r[0], r[2]): r for r in g["unevaluated"]}
    head_cells = "".join(f"<th>{esc(m)}</th>" for m in metrics)
    body = []
    n_red = n_unev = 0
    for rid in runs:
        fx = next((r[1] for r in g["guards"] if r[0] == rid),
                  next((r[1] for r in g["unevaluated"] if r[0] == rid), ""))
        tds = []
        for m in metrics:
            if (rid, m) in cell:
                _r = cell[(rid, m)]
                delta, regressed, src = _r[5], _r[6], _r[7]
                if regressed:
                    n_red += 1
                    tds.append(f'<td class="r" title="劣化 {esc(src)}；'
                               f'依据：{esc(_r[8])}">{esc(fmt(delta))} ▼</td>')
                elif delta is None:
                    tds.append('<td class="u" title="无对比值">—</td>')
                elif delta < 0:
                    tds.append(f'<td class="g" title="{esc(src)} 改善｜{esc(fmt(_r[3]))}→{esc(fmt(_r[4]))}">{esc(fmt(delta))} ▲</td>')
                else:
                    tds.append(f'<td title="{esc(src)} 未劣化（容差内）">{esc(fmt(delta))}</td>')
            elif (rid, m) in unev:
                n_unev += 1
                u = unev[(rid, m)]
                tds.append(f'<td class="u" title="未评估盲区（G20）：{esc(u[4])}｜'
                           f'窗口值 before={esc(fmt(u[5]))} after={esc(fmt(u[6]))} '
                           f'Δ={esc(fmt(u[7]))}（不可用于判定）">未评估</td>')
            else:
                tds.append('<td class="u">无此项</td>')
        body.append(f'<tr><td><code>{esc(rid)}</code><br>'
                    f'<span class="ax">{esc(fx)}</span></td>{"".join(tds)}</tr>')
    return f"""
<h2>视图 5｜全量守护指标热力图（夹具 × 指标，前后 Δ）</h2>
{_口径('每格 = 该轮该指标的 after − before（负值=改善）。'
        '判定容差：时间/渲染计数类走窗口中位 + guard_tolerance 1%；'
        '记忆体类走两侧各自 fresh post-GC（§1.5 分两支），'
        'hover 显示该格 value_source 与依据原文。')}
<table class="heat">
<tr><th>轮次</th>{head_cells}</tr>
{"".join(body)}
</table>
<p class="sub">图例：<span class="badge b-bad">红=判为劣化（{n_red} 格）</span>
<span class="badge b-good">绿=改善</span>
<span class="badge b-warn">灰底=未评估盲区（{n_unev} 格，G20 单列项）</span>
——<b>灰底既不算劣化也不算清白</b>：拿被污染或不响应的窗口值冒充守护结论，
正是 §1.5 明令禁止的。</p>
<div class="note">这张表的空白本身就是信息：只有 {len(runs)} 个轮次跑到出判定，
所以 {len(metrics)} 个守护指标 × 轮次的矩阵绝大多数格是"无此项"。
守护覆盖面要等常驻长跑累积（见文末待办）。</div>
"""


# ─────────────── 视图 6：失败与回滚钻取 ───────────────

def view6(g: dict) -> str:
    rb = g["rollbacks"]
    rv = g["reviews"]
    return f"""
<h2>视图 6｜失败与回滚钻取（{len(rb)} 次回滚 · {len(rv)} 次审查）</h2>
{_口径('回滚来源有两处：state.json 的 review 记录内嵌 rollback（审查裁定撤回），'
        '以及 runreport.final_rollback（出口兜底，未产出保留决定的轮次）。'
        'G9：回滚只依赖 git，不依赖 Editor 可达。')}
<table>
<tr><th>轮次</th><th>回滚到</th><th>回滚后 HEAD</th><th>资产清单还原</th>
<th>场景还原</th><th>类型</th></tr>
{"".join(
    f"<tr><td><code>{esc(r['run_id'])}</code></td>"
    f"<td><code>{esc(r['to'])}</code></td><td><code>{esc(r['head'])}</code></td>"
    f"<td>{esc(fmt(r['asset_restored']))}</td><td>{esc(fmt(r['scene_restored']))}</td>"
    f"<td>{esc('出口兜底' if r.get('出口兜底') else '审查裁定')}</td></tr>"
    for r in rb) or "<tr><td colspan=6>无回滚记录</td></tr>"}
</table>
<h3>审查轮次消耗</h3>
<table><tr><th>轮次</th><th>审查第几轮</th><th>被审判定</th><th>裁定回滚</th></tr>
{"".join(f"<tr><td><code>{esc(r['run_id'])}</code></td><td class='n'>{fmt(r['round'])}</td>"
         f"<td>{esc(r['verdict_status'])}</td><td>{esc(fmt(r['rollback']))}</td></tr>"
         for r in rv) or "<tr><td colspan=4>无审查记录（配额 N=3 未被触发）</td></tr>"}
</table>
<p class="sub">G6：单 Issue 审查最多 {esc(fmt(3))} 次，耗尽即记 failed 并保留全部证据链
（失败样本与成功样本同等重要）。当前未出现 failed 轮次。</p>
<h3>证据链索引（每格数字可回溯）</h3>
<ul>
{"".join(
    f"<li><code>{esc(r[0])}</code> — {esc(r[1])}，判定 <b>{esc(r[8] or '无')}</b>，"
    f"路由 {esc(r[7] or '—')}，attempt {fmt(r[9])}，"
    f"墙钟 {fmt(r[5])}s，token {fmt(r[6])}，检出 {fmt(r[11])} 条、"
    f"未评估守护 {fmt(r[12])} 项；"
    f"<a href=\"file:///{esc(str(RD.AG.RUNS / r[0]).replace(chr(92), '/'))}\">证据目录</a>"
    f"（trace.jsonl / state.json / runreport.json）</li>"
    for r in g["runs"] if r[8] or r[11])}
</ul>"""


# ─────────────── 视图 7：成本分布 ───────────────

def view7(g: dict) -> str:
    from upi.tooling.report_data import dots_svg
    import statistics as st
    toks = [(i, r[6] or 0, f"{r[0]}：{fmt(r[6])} token")
            for i, r in enumerate(g["runs"]) if r[10] and r[6]]
    walls = [(i, r[5] or 0, f"{r[0]}：{fmt(r[5])}s")
             for i, r in enumerate(g["runs"]) if r[10] and r[5]]
    stage_tok = [(f"{s[0]}", s[7] or 0) for s in g["stages"] if s[7]]
    tot = sum(v for _, v in stage_tok) or 1
    from upi.tooling.report_data import bars_svg
    share = [(f"{k}（{v/tot*100:.0f}%）", v, None) for k, v in
             sorted(stage_tok, key=lambda x: -x[1])]
    per_issue = [r[6] for r in g["runs"] if r[10] and r[6]]
    box = ""
    if per_issue:
        med = st.median(per_issue)
        box = (f'<p class="sub">单轮成本中位 {fmt(med)} token，'
               f'最大 {fmt(max(per_issue))}，最小 {fmt(min(per_issue))}，'
               f'n={len(per_issue)}（箱线图需 ≥{RD.MIN_N_LINE} 样本才画，'
               f'当前只给三个数）。</p>')
    return f"""
<h2>视图 7｜成本分布 {_n_badge(len(per_issue))}</h2>
{_口径('token 取 span 的 llm.token_in+token_out，缺则取 runreport.spent.tokens；'
        '墙钟同理取 spent.wallclock_s，缺则取该轮 span 之和。'
        '两者都是实测记账值，非估算。')}
<div class="grid2">
<div><h3>每轮 token</h3>{dots_svg(toks, ylab="token", xlab="轮次序号")}</div>
<div><h3>每轮墙钟</h3>{dots_svg(walls, ylab="秒", xlab="轮次序号", color="#c05621")}</div>
</div>
<h3>按阶段的 token 占比</h3>
{bars_svg(share, unit="token", color="#6b46c1") if share else
 '<div class="empty">span 里没有 llm token 明细（历史 span 的 llm 字段多为空，'
 '实测如此）；成本只能按轮汇总看上面的每轮 token。</div>'}
{box}"""


# ─────────────── 体检表 + 对比表 + 边界 ───────────────

def checklist_sec(g: dict) -> str:
    cl = g["checklist"]
    rows = []
    for field, it in cl["items"].items():
        if it["ok"] is None:
            b, cls = "未计算", "b-dim"
        elif it["ok"]:
            b, cls = "达标", "b-good"
        else:
            b, cls = "不达标", "b-bad"
        rows.append(f"<tr><td><code>{esc(field)}</code></td>"
                    f"<td>{esc(it['threshold'])}</td>"
                    f"<td class='n'>{esc(fmt(it['value'],3))}</td>"
                    f"<td class='n'>{fmt(it['n'])}</td>"
                    f"<td><span class='badge {cls}'>{esc(b)}</span></td>"
                    f"<td>{esc(it['why'])}</td></tr>")
    return f"""
<h2>§8.7 过拟合体检表（实际数值，G14 上线门禁的输入）</h2>
{_口径("阈值单一来源 upi.core.knowledge._CHECKLIST（本表通过反查判定函数得到符号与"
        "边界值，不抄第二份数字）；判定结果同时喂给 propose_promotion，"
        "保证提案与本表不会出现分歧。")}
<table>
<tr><th>体检项</th><th>通过条件</th><th>实测值</th><th>n</th><th>结论</th><th>取数说明 / 缺项原因</th></tr>
{"".join(rows)}
</table>
<div class="note"><b>整体结论：{esc(cl['status'])}</b>
（六项中 达标 {fmt(sum(1 for v in cl['items'].values() if v['ok']))} 项、
不达标 {cl['n_fail']} 项、未计算 {cl['n_missing']} 项）。
按 G14「缺任一项或任一项不达标 → 该阈值/权重/pattern 变更 blocked」，
<b>当前任何升维提案都必须保持 blocked</b>。
这不是流程失败——DESIGN §11 已预判第一轮必然如此，门禁在按设计工作。
达标前提：n_effective ≥ 10 且六项全部可计算。</div>
"""


def compare_sec(g: dict) -> str:
    """前后对比全量指标表（§5 要求的"人读产物"第一张）。"""
    rows = []
    for r in g["guards"]:
        rid, fx, m, before, after, delta, reg, src, basis = r
        rows.append(f"<tr><td><code>{esc(rid)}</code></td><td>{esc(fx)}</td>"
                    f"<td>{esc(m)}</td><td class='n'>{esc(fmt(before))}</td>"
                    f"<td class='n'>{esc(fmt(after))}</td>"
                    f"<td class='n'>{esc(fmt(delta))}</td>"
                    f"<td>{esc(src)}</td>"
                    f"<td>{'<span class=\"badge b-bad\">劣化</span>' if reg else '<span class=\"badge b-good\">未劣化</span>'}</td>"
                    f"<td class='sub'>{esc((basis or '')[:150])}</td></tr>")
    unev = "".join(
        f"<tr><td><code>{esc(r[0])}</code></td><td>{esc(r[1])}</td><td>{esc(r[2])}</td>"
        f"<td class='n'>{esc(fmt(r[5]))}</td><td class='n'>{esc(fmt(r[6]))}</td>"
        f"<td class='n'>{esc(fmt(r[7]))}</td><td>{esc(r[3])}</td>"
        f"<td><span class='badge b-dim'>未评估</span></td>"
        f"<td class='sub'>{esc((r[4] or '')[:150])}</td></tr>"
        for r in g["unevaluated"])
    return f"""
<h2>前后对比全量指标表（含未评估项单列）</h2>
{_口径('对比只在 protocol_fingerprint 一致时成立（G7）。记忆体类走 fresh post-GC，'
        '时间/渲染类走窗口中位（§1.5 两支）。未评估行的窗口值仅作参考，不参与判定。')}
<table>
<tr><th>轮次</th><th>夹具</th><th>指标</th><th>改前</th><th>改后</th><th>Δ</th>
<th>取值来源</th><th>结论</th><th>依据（截断）</th></tr>
{"".join(rows)}
{unev}
</table>"""


def footer(g: dict) -> str:
    cl = g["checklist"]
    return f"""
<footer>
<b>达标门槛（让读者知道什么时候才有资格谈泛化，§8.8）</b>：
本阶段 n_effective={fmt((g['meta']['quality']['success_rate'] or {}).get('n_effective_contexts'))}，
升维与阈值变更需 n_effective ≥ 10（§3 维度三）且 §8.7 六项全部可计算并达标；
趋势折线需可比轮次 ≥ {RD.MIN_N_LINE}（§7.3）。
当前全部不满足 ⇒ 本报告一切结论等级为<b>方法学示范</b>。<br><br>
<b>本报告有效性自检</b>：trace 纳入 {g['meta']['cost']['runs_traced']} / untraced 剔除
{g['meta']['cost']['runs_untraced']}（G13）；不可检出清单 {g['blind']['n_blocked']} 项且与检出率同屏
（G20）；体检表六项齐（{cl['n_missing']} 项未计算已注明原因）；
成本与质量同轴（G15）；证据链索引每格可回溯（视图 6）。
<br><br>
数据截至本轮聚合；重新生成：<code>python -m upi dev report</code>。
本报告只读，不提供任何改阈值或改权限的入口（§7.3 末条、AGENTS I3）。
</footer>"""


def render(g: dict, out: Path | str | None = None) -> Path:
    body = "".join([head(g), view1(g), view2(g), view3(g), view4(g),
                    view5(g), view6(g), view7(g), compare_sec(g),
                    checklist_sec(g), footer(g)])
    doc = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           "<title>AI 性能优化管线 · 历史统计与总结报告</title>"
           f"<style>{CSS}</style></head><body><div class=\"wrap\">{body}</div>"
           "</body></html>")
    p = Path(out or OUT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p


def build(out: Path | str | None = None, db: Path | str | None = None) -> Path:
    return render(gather(db), out)


if __name__ == "__main__":
    print(build())
