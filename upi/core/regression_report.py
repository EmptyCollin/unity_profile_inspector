"""回归报告（I4b2b2b）：基线/计划/verdict/守护的只读 HTML + JSON。

不是新证据源：所有数字来自 `runs/_regression` 的 suite/matrix/verdicts。
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional

from upi.core import events as E
from upi.core.regression import (RegressionPlan, RegressionStore,
                                 VerifiedRecord)

# G20：报告必须与检出率同屏声明不可检出项；缺该清单的报告无效。
BLIND_SPOTS = [
    {"id": "gpu_attribution", "why": "GPU 尖峰逐笔归因需 EX-1/B 档或人工"},
    {"id": "unamplified_code", "why": "未放大的代码风险需 C+ 扫描"},
    {"id": "perception", "why": "感知类退化需 M7 T8"},
    {"id": "memory_guard", "why": "记忆体守护面窄，主要靠 monoUsedBytes"},
    {"id": "batches", "why": "batches 注入无汇总句柄，SRP 常态两侧同零"},
]


def build_report(store: RegressionStore, suite_id: str) -> dict:
    suite = store.load_suite(suite_id)
    matrix = store.load_matrix(suite_id)
    plan = RegressionPlan.from_matrix(matrix) if matrix else None
    verdicts = store.list_verdicts(suite_id)
    verified = sum(1 for v in verdicts
                   if v.verdict.verification_status == "verified")
    guard_metrics = sorted({k for r in (matrix.runs if matrix else [])
                            for k in (r.guards or {})})
    return {
        "report_version": "regression-run-report/1",
        "mode": "regression",
        "verification_status": "verified" if verified else "unverified",
        "suite": suite.model_dump() if suite else None,
        "runs": [r.model_dump() for r in (matrix.runs if matrix else [])],
        "protocol": matrix.protocol() if matrix else "",
        "guard_metrics": guard_metrics,
        "plan": plan.model_dump() if plan else None,
        "verdicts": [v.model_dump() for v in verdicts],
        "verified_count": verified,
        "blind_spots": BLIND_SPOTS,
        "generated_at": E.now(),
    }


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def render_html(data: dict) -> str:
    suite = data.get("suite") or {}
    plan = data.get("plan") or {}
    runs = data.get("runs") or []
    verdicts = data.get("verdicts") or []
    rows = "".join(
        f"<tr><td>{_esc(r.get('run_id'))}</td><td>{_esc(r.get('run_index'))}</td>"
        f"<td>{_esc(r.get('event_count'))}</td>"
        f"<td>{_esc(len(r.get('clusters') or []))}</td>"
        f"<td>{_esc(len(r.get('guards') or {}))}</td>"
        f"<td><code>{_esc(r.get('protocol_fingerprint'))}</code></td></tr>"
        for r in runs)
    items = "".join(
        f"<tr><td><code>{_esc(i.get('metric'))}</code></td><td>{_esc(i.get('shape'))}</td>"
        f"<td>{_esc(i.get('reproduction_rate'))}</td><td>{_esc(i.get('first_rel'))}</td>"
        f"<td>{_esc(i.get('status'))}</td></tr>"
        for i in (plan.get("items") or []))
    vrows = "".join(
        f"<tr><td>{_esc(v.get('record_id'))}</td>"
        f"<td>{_esc((v.get('verdict') or {}).get('status'))}</td>"
        f"<td>{_esc((v.get('verdict') or {}).get('verification_status'))}</td>"
        f"<td>{_esc(', '.join((v.get('verdict') or {}).get('unassessed_guards') or []))}</td>"
        f"<td>{_esc((v.get('verdict') or {}).get('reason'))}</td></tr>"
        for v in verdicts)
    g = []
    for r in runs:
        for k, val in (r.get("guards") or {}).items():
            g.append(f"<tr><td>{_esc(r.get('run_id'))}</td><td><code>{_esc(k)}</code></td>"
                     f"<td>{_esc(val)}</td></tr>")
    guards = "".join(g) or "<tr><td colspan='3'>无守护数据</td></tr>"
    blind = "".join(f"<li><code>{_esc(b.get('id'))}</code>：{_esc(b.get('why'))}</li>"
                    for b in (data.get("blind_spots") or [])) or "<li>清单缺失（报告无效）</li>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>UPI 回归报告 - {_esc(data.get('suite', {}).get('suite_id') if data.get('suite') else '')}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1d2733}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:22px}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0}}
th,td{{border:1px solid #d8dee7;padding:6px;text-align:left}}
th{{background:#f3f6fa}} code{{font-family:Consolas,monospace}}
.bad{{color:#a33}} .ok{{color:#176b3a}} .muted{{color:#667}}</style></head><body>
<h1>UPI 回归报告：{_esc(suite.get('name') or suite.get('suite_id'))}</h1>
<p class="muted">生成 {_esc(data.get('generated_at'))} · 形态 <b>{_esc(data.get('mode'))}</b>
 · 验证等级 <b>{_esc(data.get('verification_status'))}</b>
 · 协议 <code>{_esc(data.get('protocol'))}</code>
 · 守护指标 {_esc(len(data.get('guard_metrics') or []))} 个
 · verified {_esc(data.get('verified_count'))} 条</p>
<h2>基线 runs</h2>
<table><thead><tr><th>run</th><th>index</th><th>事件数</th><th>cluster</th><th>守护</th><th>协议</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6">无</td></tr>'}</tbody></table>
<h2>计划（复现率 ≥ 2/3）</h2>
<table><thead><tr><th>指标</th><th>波形</th><th>复现率</th><th>first_rel</th><th>状态</th></tr></thead>
<tbody>{items or '<tr><td colspan="5">无可执行计划</td></tr>'}</tbody></table>
<h2>verdict 审计（含失败）</h2>
<table><thead><tr><th>record</th><th>status</th><th>verification</th><th>未评估守护</th><th>原因</th></tr></thead>
<tbody>{vrows or '<tr><td colspan="5">尚无 verdict</td></tr>'}</tbody></table>
<h2>守护指标</h2>
<table><thead><tr><th>run</th><th>metric</th><th>中位</th></tr></thead>
<tbody>{guards}</tbody></table>
<h2>不可检出清单（G20）</h2>
<ul>{blind}</ul>
<p class="muted">本报告只读；verified 只在“同协议 + 目标消失 + 无新 cluster + 守护不劣化”时成立。
未跑真实 replay 的记录不构成绩效结论。sample 候选不是 finding。</p>
</body></html>"""


def write_report(store: RegressionStore, suite_id: str) -> Path:
    d = store.root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{suite_id}.html"
    p.write_text(render_html(build_report(store, suite_id)), encoding="utf-8")
    return p


def write_json_report(store: RegressionStore, suite_id: str) -> Path:
    d = store.root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{suite_id}.json"
    p.write_text(json.dumps(build_report(store, suite_id), ensure_ascii=False,
                            indent=2), encoding="utf-8")
    return p


def report_path(store: RegressionStore, suite_id: str) -> Path:
    return store.root / "reports" / f"{suite_id}.html"
