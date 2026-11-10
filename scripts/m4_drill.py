#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m4_drill.py —— M4 合并演练：状态机全链路 + 真实归因 + 人工批准回路。

一笔成本验三件事（用户拍板的路线）：
  M2 归因链：diagnose 节点走真模型（factor_judge 首次端到端，逐候选判定）
  状态机  ：LangGraph 七节点全链路首跑（detect→diagnose→plan→fix→…→record）
  M4 回路 ：blocked 档夹具代码 ⇒ fix 节点自动出五件套工单并停住，
            用户写 decision.json 批准后阶段二从断点续跑（不重烧采样/归因）

用法（两阶段）：
  python scripts/m4_drill.py --phase1            # 检测→真归因→计划→出工单，停
  查看 runs/m4_drill_<stamp>/approvals/*/request.md，确认后写 decision.json：
    {"decision":"approve","approver":"<名字>","note":"<批的具体内容>"}
  python scripts/m4_drill.py --phase2 --run m4_drill_<stamp>   # 批准→修→验→回滚演练

--dry-run：只验 diff 命中与工单可渲染，不跑真模型不写入（先烧这个，别先烧大的）。
成本预期：阶段一 ≈ detect 1 分钟 + diagnose 5 个小请求（积分）；
          阶段二 ≈ 一次干净 attempt 约 7 分钟（before+after 各 2.4min 重采 + 编译 + 判定，实测值），LLM 零消耗。重试会成倍增加。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ⚠️ 阶段 7 后 nodes 不再导出 CSFILE/PROJECT/SCENE 这些模块级路径常量
#   （值会随 profile 变，模块常量等于在导入瞬间冻结）。演练脚本改从档案现取。
from upi.agents.nodes import _plan_for                     # noqa: E402
from upi.agents.orchestrator import Runner                 # noqa: E402
from upi.config.project_profile import active_profile      # noqa: E402

_PROF = active_profile()
FIXTURE = _PROF.fix_subject
CLEAN = _PROF.clean_subject
DETECTOR = "DET-CPU-GC-FRAMEALLOC"
CSFILE = _PROF.subject_file


def dry_run() -> int:
    print("== dry-run：只验命中与工单渲染，不跑真模型/不写入/不采样 ==")
    src = (ROOT / CSFILE).read_text(encoding="utf-8")
    # 模板必须先于命中循环构造：此前顺序写反，Python 把 CPU_PLAN 判为局部
    # 变量 → 使用前赋值未发生 → UnboundLocalError（顺序错，不是逻辑错）
    from upi.core.approval import build_request, render_markdown
    from upi.core.detector import DEFAULT_REGISTRY
    from upi.core.models import AttributionFactors, Hypothesis
    det = next(d for d in DEFAULT_REGISTRY if d.id == DETECTOR)
    CPU_PLAN = _plan_for(det.anchor_patterns[0])
    if CPU_PLAN is None:
        print("   !! 模板缺失")
        return 1
    for i, d in enumerate(CPU_PLAN.diffs):
        n = src.count(d["find"])
        print(f"   diff[{i}] 命中 {n}/{d['expect_count']}")
        if n != d["expect_count"]:
            return 1

    # 干跑工单的证据也走判据链（compare 只搬已采数据）：dry-run 不产数值，
    # 不冒充归因——hypothesis 置信度如实填 0 并注明占位（I1/§5）
    from scripts.m1_detect import (apply_calibration, latest_calibration,
                                   load_multwin, stats_from_windows, compare)
    mw = load_multwin()
    if mw is None:
        print("   !! 无标定束")
        return 1
    # §8.1 落地：检测端阈值只接受标定文件覆盖，注册表初值一律作废。
    # 干跑也必须走 apply_calibration——上一版直接拿初值判检出，
    # min_magnitude=100000（初值）vs 20000（标定值）导致误报"未检出"。
    det = det.model_copy(deep=True)      # 不污染全局注册表（干跑无副作用）
    cal = latest_calibration()
    if cal is None:
        print("   !! 无标定文件（§8.1 禁止未标定检测）")
        return 1
    if apply_calibration([det], cal) != 1:
        print("   !! 标定未覆盖该检测器")
        return 1
    print(f"   标定注入：min_magnitude={det.thresholds.min_magnitude:g}"
          f"（{Path(cal['_path']).name}）")
    fixs = {k: stats_from_windows(v) for k, v in mw["fixtures"].items()
            if "windows" in v}
    issue, detail = compare(det, fixs[FIXTURE], fixs[CLEAN])
    if issue is None:
        print("   !! 当前夹具未检出（detail:", detail.get("verdict"), "）")
        return 1
    hyp = Hypothesis(root_cause=f"（dry-run 占位：待真模型判定）{det.anchor_patterns[0]}",
                     code_anchors=[CSFILE + ":21"],
                     # 默认因子全 0 = 未判定，dry-run 不冒充归因结论（I1）
                     attribution_factors=AttributionFactors(),
                     confidence=0.0,
                     expected_effect={"metric": det.metric,
                                      "note": "dry-run 不给量化预期"})
    req = build_request(run_id="dryrun", issue=issue, hypothesis=hyp,
                        plan=CPU_PLAN, permission_tier="blocked",
                        reason_not_auto="夹具脚本属 blocked 档（§3 维度一：永远人工）",
                        git_ref="（dry-run 无快照，正式跑时写真实 tag）")
    md = render_markdown(req)
    out = ROOT / "runs" / "_dryrun_approval.md"
    out.write_text(md, encoding="utf-8")
    print(f"   工单渲染样例：{out}（{len(md)} 字符，五件套齐全）")
    print("   dry-run 通过。真跑顺序：--phase1 → 审工单写 decision.json → --phase2")
    return 0


def phase1(run_id: str, live_llm: bool) -> int:
    print(f"== 阶段一 {run_id}：detect → diagnose(live={live_llm}) → plan → 出工单 ==")
    runner = Runner(run_id, FIXTURE, budget={
        "max_review_rounds": 1, "max_attempts_per_issue": 1,
        "wallclock_s": 3600, "max_tokens": 120_000})
    from upi.agents.nodes import make_nodes
    nodes = make_nodes(runner.dir, live_llm=live_llm, approval_note="")
    p1_final = runner.run(nodes)
    st = json.loads((runner.dir / "state.json").read_text(encoding="utf-8"))
    ar = st.get("approval_request")
    if not ar:
        print("!! 阶段一未走到出工单。route:", p1_final.get("route"),
              "error:", str(p1_final.get("error"))[:200])
        print("   状态与 trace:", runner.dir / "state.json")
        return 1
    rid = Path(ar["md"]).parent.name
    print(f"   工单已递交：{ar['md']}")
    print(f"   待批目录：runs/{run_id}/approvals/{rid}/")
    print("   请审阅 request.md 五件套，同意后写 decision.json：")
    print('   {"decision":"approve","approver":"<你的名字>","note":"<批的具体内容>"}')
    print(f"   然后跑：python scripts/m4_drill.py --phase2 --run {run_id}")
    return 0


def phase2(run_id: str) -> int:
    print(f"== 阶段二 {run_id}：验批准 → fix 续跑（修→验→记录） ==")
    runner = Runner(run_id, FIXTURE)   # 同 run_id：trace 续写、state 续读
    st = json.loads((runner.dir / "state.json").read_text(encoding="utf-8"))
    ar = st.get("approval_request")
    if not ar:
        print("!! state.json 无工单记录，阶段二无从续起")
        return 1
    from upi.core.approval import ApprovalStore
    store = ApprovalStore(runner.dir)
    rid = Path(ar["md"]).parent.name
    dec = store.load_decision(rid)
    if dec is None:
        print(f"!! 未找到有效决定（或不合格）：{store._dir(rid)/'decision.json'}")
        print("   通道规则：无 decision=未批，approve 缺批准人/内容=视同未批。")
        return 1
    if dec.decision != "approve":
        print(f"   决定为 {dec.decision}（{dec.approver}）——演练按拒绝路径收尾，"
              "Issue 记 needs_human，无写入无采样消耗。")
        return 0
    print(f"   批准有效：{dec.approver}：{dec.note[:80]}")

    from upi.agents.nodes import make_nodes
    nodes = make_nodes(runner.dir, live_llm=False, approval_note="")
    # 两阶段续跑：入口 fix；归因/计划产物从 state.json 复用（不重烧积分）。
    # fix 节点会重读 decision.json（批准通道幂等），拿到文本后采基线→写入→验证。
    runner.resume(nodes, entry="fix")   # 结果从 state.json 读回
    st2 = json.loads((runner.dir / "state.json").read_text(encoding="utf-8"))
    v = st2.get("verdict") or {}
    print("\n== 续跑结果 ==")
    print("   route:", st2.get("route"), "| attempts:", st2.get("attempts"))
    if v:
        t = v.get("target", {})
        print(f"   目标 {t.get('metric')}: {t.get('before')} → {t.get('after')}"
              f" Δ{t.get('delta')} significant={t.get('significant')}")
        print("   判定:", v.get("status"))
        reg = [g["metric"] for g in v.get("guards", []) if g.get("regressed")]
        print("   守护劣化:", reg or "无")
        recs = st2.get("records", [])
        last_v = next((r for r in reversed(recs) if r.get("kind") == "verify"), {})
        print("   记忆体盲区:", [q["metric"] for q in last_v.get("unevaluated", [])]
              or "无")
    else:
        print("   未走到 verdict。error:", str(st2.get("error"))[:200])
    # 回滚演练收尾（同彩排惯例：演示后复位缺陷夹具，保留检出原料）
    from upi.tooling import snapshot as snapmod
    from upi.agents.nodes import _snap_from
    if st2.get("snapshot"):
        try:
            rb = snapmod.rollback(_snap_from(st2),
                                  ROOT / "fixtures" / "upi-fixture",
                                  ROOT / "fixtures" / "upi-fixture" /
                                  "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity")
            print("   回滚演练: 资产复位", rb["asset_manifest_restored"],
                  "场景复位", rb["scene_restored"], "未碰Editor", not rb["editor_touched"])
        except Exception as e:  # noqa: BLE001
            print("   !! 回滚演练失败:", str(e)[:160])
    print("   完整状态:", runner.dir / "state.json", "| trace:", runner.tracer.path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--phase1", action="store_true")
    g.add_argument("--phase2", action="store_true")
    ap.add_argument("--run", default=None, help="phase2 指定 run_id")
    ap.add_argument("--offline-llm", action="store_true",
                    help="phase1 用离线替身归因（省积分，验流程用；默认真模型）")
    args = ap.parse_args()

    if args.dry_run:
        return dry_run()
    if args.phase1:
        run_id = "m4_drill_" + time.strftime("%Y%m%d_%H%M%S")
        return phase1(run_id, live_llm=not args.offline_llm)
    if args.phase2:
        if not args.run:
            runs = sorted((ROOT / "runs").glob("m4_drill_*"))
            if not runs:
                print("!! 无阶段一产物，先跑 --phase1")
                return 1
            args.run = runs[-1].name
            print(f"   自动选取最近演练：{args.run}")
        return phase2(args.run)
    return 2


if __name__ == "__main__":
    sys.exit(main())
