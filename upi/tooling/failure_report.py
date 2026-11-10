"""失败出口的总结报告（阶段 8 #24：修不成 ⇒ 出"下限画像 + 试过什么 + 为什么 + 下一步"）。

设计定位（用户定调的循环）：提高下限 ⇒ 瓶颈暴露 ⇒ 尝试修复 ⇒ 修不成（配额/
假设穷尽）不该只剩一句 error 字符串，而要产出一份**可指导下一步**的人读报告。
它与 runreport.json 的分工：那边是给统计层的机器终态（判定分布/成本），
这边是给**人**的叙事——"下一步可能可以优化的方向"只有人（或下一轮的
Diagnose）消费，所以内容取向完全不同：不重复 JSON 里已有的数字，而是把
records 里的轨迹组装成"试过什么、停在哪、还剩什么"。

纯函数、只吃 RunState dict：escalate 调用它，测试直接喂构造态断言内容。
不做成节点的原因与 record/escalate 同构——它是出口的附属产物，不该有
自己的 span 污染轮次计数（interaction 的教训：新状态载体要问"谁会误读它"）。
"""
from __future__ import annotations

from typing import Optional


def failure_summary_md(st: dict, detection: Optional[dict] = None) -> str:
    """从终态 RunState 生成失败总结（markdown）。

    detection：本轮 detection.json 的解析结果（可选）。有则附"下限画像"——
    本轮测到的水位/越线事实是报告的对照面；缺（比如 detect 之前就挂了）
    就如实写"无本轮检出数据"，不编。
    """
    issue = st.get("issue") or {}
    recs = st.get("records") or []
    fixes = [r for r in recs if r.get("kind") == "fix"]
    reviews = [r for r in recs if r.get("kind") == "review"]
    err = str(st.get("error") or "")
    v = st.get("verdict") or {}

    L: list[str] = []
    L.append(f"# 修复未成报告 · {issue.get('fixture', '?')}")
    L.append("")
    L.append(f"- run: `{st.get('run_id', '?')}`  出口原因: `{err[:160] or '（未记录）'}`")
    L.append(f"- 问题: metric={issue.get('metric')} 量级={issue.get('magnitude')} "
             f"detector={issue.get('detector')}")
    spent = st.get("spent") or {}
    if spent:
        L.append(f"- 已消耗: attempts={st.get('attempts', len(fixes))} "
                 f"review={len(reviews)} wallclock={spent.get('wallclock_s')}s "
                 f"tokens={spent.get('tokens', 0)}")

    # ── 下限画像（本轮检出全景）──
    L.append("\n## 本轮下限画像")
    if detection:
        nb = detection.get("noise_basis")
        L.append(f"- 噪声带来源: {nb}（L0=无同期基准，只跑绝对线；"
                 "误报率 not_measurable 属预期）" if nb else "- 噪声带来源: （旧轮产物无此字段）")
        results = detection.get("results") or {}
        for fid, r in results.items():
            mark = {"detected": "越线", "not_found": "达标",
                    "insufficient_evidence": "未评估"}.get(r.get("verdict"), r.get("verdict"))
            L.append(f"  - {fid}: {mark}（{r.get('metric')} 实测={r.get('fixture')} "
                     f"参照={r.get('clean')}）")
        fl = detection.get("floors") or {}
        if fl.get("enabled"):
            for b in fl.get("breached") or []:
                L.append(f"  - {b['subject']}: 绝对下限越线 {b['metric']} "
                         f"{b['op']}{b['line']} 实测={b['stat']}")
            for u in fl.get("unevaluated") or []:
                L.append(f"  - {u['subject']}: 下限未评估 {u['metric']}（{u['why']}）")
    else:
        L.append("- 无本轮检出数据（detect 未产出 detection.json），画像缺失如实声明。")

    # ── 试过的方向（Fix 侧轨迹）──
    L.append("\n## 试过的方向")
    if not fixes:
        L.append("- 没有任何一次修复落地（挂在 diagnose/plan/审批之前，方向清单为空）。")
    for i, r in enumerate(fixes, 1):
        ex = r.get("execute") or {}
        stx = ex.get("status", "?")
        diff_n = len((ex.get("diffs") or ex.get("applied") or [])) or "?"
        L.append(f"{i}. `{r.get('root_cause', '?')}` — 执行={stx} diff={diff_n} "
                 f"git={str(r.get('git_ref'))[:24]}")
        if stx == "rejected":
            note = ((ex.get("decision") or {}).get("note")) or ""
            L.append(f"   人工拒绝理由: {note[:120]}")

    # ── 为什么没成（判定链原文，不解读）──
    L.append("\n## 为什么没成")
    if v:
        L.append(f"- 最后一次判定: status={v.get('status')} "
                 f"delta={((v.get('target') or {}).get('delta'))} "
                 f"significant={((v.get('target') or {}).get('significant'))}")
        for g in (v.get("guards") or [])[:6]:
            if g.get("regressed"):
                L.append(f"- 守护劣化: {g.get('metric')} Δ={g.get('delta')}")
    else:
        L.append("- 未跑到判定（更早的环节挂了，见出口原因）。")
    for r_ in reviews:
        L.append(f"- 审查第 {r_.get('round')} 轮: verdict={r_.get('verdict_status')} "
                 f"决定={r_.get('decision', '')}")

    # ── 下一步候选（未试假设 / 穷尽声明 / 转人工线索）──
    # 这条分流的另一半（"手段用尽 vs 尚未尝试"）就在这里说清：plan 节点的
    # "候选假设已穷尽"出口 ≠ 预算耗尽出口，报告必须把两者分开呈现，
    # 否则人看不出"是该补方案模板"还是"该换思路"。
    L.append("\n## 下一步候选")
    tried = {r.get("root_cause") for r in fixes}
    remain = [h for h in (st.get("hypotheses") or [])
              if h.get("root_cause") not in tried]
    if "穷尽" in err:
        L.append(f"- 假设已穷尽（{len(tried)} 条全试过）——瓶颈在**修复手段**而非归因："
                 "需要为新机制补 playbook 模板，或人工给出方向后重跑。")
    elif remain:
        for h in remain:
            L.append(f"- 未试假设: `{h.get('root_cause')}`"
                     f"（置信={h.get('confidence')}）")
    else:
        L.append("- 假设清单已消费完；若判定是 regressed，优先复核守护指标劣化项的因果。")
    if st.get("approval_request"):
        L.append("- 有一张停在人工审批的工单：结论在批准/拒绝后才成立。")
    L.append("")
    L.append("> 本报告由失败出口自动生成；数字回指 detection.json / trace.jsonl，"
             "未经新的采样或估计（I1）。")
    return "\n".join(L)
