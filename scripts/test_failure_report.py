"""#24 失败总结报告测试：纯函数内容 + escalate 出口触发条件（含真实节点直测）。

两条线都要测，因为它们会各自独立地坏：
  · 纯函数坏 ⇒ 报告出来但内容是编的/缺段；
  · 出口触发坏 ⇒ **成功的轮次也生成"修复未成"报告**（撒谎），或失败的轮次
    静默不出报告（#24 的意义全丢）。
第二线必须直测 make_nodes 里的**真** escalate，不能像 test_orchestrator 那样
把 escalate 打桩——打桩就正好绕过了本批新加的那段判据。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.nodes import make_nodes                       # noqa: E402
from upi.tooling.failure_report import failure_summary_md     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  ← {str(detail)[:130]}"))


def base_state(**over):
    st = {
        "run_id": "run-x", "fixture": "F-GPU-01", "attempts": 2,
        "spent": {"wallclock_s": 90.0, "tokens": 1200},
        "issue": {"fixture": "F-GPU-01", "metric": "drawCalls",
                  "magnitude": 80.0, "detector": "DET-GPU-BATCHBREAK"},
        "records": [
            {"kind": "fix", "git_ref": "upi/attempt-1", "root_cause": "合批被打断：材质混用",
             "execute": {"status": "applied", "applied": [{"file": "a.cs"}, {"file": "b.cs"}]}},
            {"kind": "review", "round": 1, "verdict_status": "not_significant"},
            {"kind": "fix", "git_ref": "upi/attempt-2", "root_cause": "SRP 批处理未开",
             "execute": {"status": "rejected", "decision": {"note": "会改画面，拒"}}},
        ],
        "hypotheses": [{"root_cause": "合批被打断：材质混用", "confidence": 0.8},
                       {"root_cause": "SRP 批处理未开", "confidence": 0.6},
                       # 第三条从未进 fix ⇒ 报告应把它列为"下一步候选"，
                       # 已试过的两条不该再出现（第一版数据只有两条、全试过，
                       # 断言与数据自相矛盾，测的是我自己写错的期望）。
                       {"root_cause": "显存带宽受限（需真机验证）", "confidence": 0.4}],
        "verdict": {"status": "not_significant",
                    "target": {"delta": 3.0, "significant": False},
                    "guards": [{"metric": "batches", "delta": 0.02, "regressed": True}]},
        "route": "needs_human", "error": "budget_exceeded(review_rounds)",
    }
    st.update(over)
    return st


def main() -> int:
    print("== ① 纯函数：画像/轨迹/判定/候选 四段齐备 ==")
    det = {"noise_basis": "L0",
           "results": {"F-GPU-01": {"verdict": "detected", "metric": "drawCalls",
                                    "fixture": 127.0, "clean": None},
                       "F-CPU-01": {"verdict": "not_found", "metric": "gc",
                                    "fixture": 1.0, "clean": 1.0},
                       "F-ANIM-01": {"verdict": "insufficient_evidence", "metric": "anim",
                                     "fixture": 9.0, "clean": None}},
           "floors": {"enabled": True,
                      "breached": [{"subject": "F-GPU-01", "metric": "drawCalls",
                                    "op": "<=", "line": 50, "stat": 127.0}],
                      "unevaluated": [{"subject": "F-CPU-01", "metric": "triangles",
                                       "why": "本轮无 triangles 逐窗口数据"}]}}
    md = failure_summary_md(base_state(), det)
    check("标题带被测机制", "# 修复未成报告 · F-GPU-01" in md)
    check("下限画像：检出/达标/未评估三态都翻译成人话",
          "越线" in md and "达标" in md and "未评估" in md)
    check("L0 声明误报率不可测（G15 成对展示的产物层落实）",
          "not_measurable" in md)
    check("绝对下限越线与未评估项分列",
          "绝对下限越线 drawCalls <=50" in md and "下限未评估 triangles" in md)
    check("试过方向含根因、执行状态与 diff 数",
          "合批被打断：材质混用" in md and "执行=applied" in md
          and "执行=rejected" in md and "diff=2" in md)
    check("人工拒绝理由回显（不丢批语）", "会改画面，拒" in md)
    check("为什么没成：末次判定 + 守护劣化 + 审查轮次",
          "status=not_significant" in md and "守护劣化: batches" in md
          and "审查第 1 轮" in md)
    check("下一步候选不含已试过的假设",
          "未试假设" in md and md.count("合批被打断：材质混用") >= 1
          and "未试假设: `SRP 批处理未开`" not in md)

    print("== ② 分流两半要能分开（穷尽 vs 预算）==")
    md2 = failure_summary_md(base_state(error="候选假设已穷尽（2 条试过），转人工"), det)
    check("假设穷尽 ⇒ 指向'补修复手段'而不是'换思路'",
          "假设已穷尽" in md2 and "playbook 模板" in md2)
    check("预算耗尽 ⇒ 不谎称穷尽，仍列剩余方向",
          "假设已穷尽" not in failure_summary_md(base_state(), det))

    print("== ③ 缺数据要如实声明，不编 ==")
    md3 = failure_summary_md(base_state(detection_ref=None), None)
    check("无 detection 数据 ⇒ 声明画像缺失（不静默跳过该段）",
          "无本轮检出数据" in md3 and "## 本轮下限画像" in md3)
    md4 = failure_summary_md(base_state(records=[], attempts=0), det)
    check("一次修复都没落地 ⇒ 明说方向清单为空", "没有任何一次修复落地" in md4)
    md5 = failure_summary_md(base_state(verdict=None), det)
    check("没跑到判定 ⇒ 说明挂在更早环节", "未跑到判定" in md5)

    print("== ④ 出口触发条件（直测真 escalate 节点，不打桩）==")
    tmp = Path(tempfile.mkdtemp())
    try:
        nodes = make_nodes(tmp / "run-fail")
        # 非成功终态（applied 之后没 resolved，但 review 已回滚 ⇒ 不再动 git）
        st = base_state(rollback_done=True)
        st["records"] = [dict(r) for r in st["records"]]
        st["records"][1]["rollback"] = {"ok": True}   # 已有回滚 ⇒ 出口不重复回滚
        nodes["escalate"](st)
        f1 = tmp / "run-fail" / "failure_summary.md"
        check("失败终态 ⇒ 生成 failure_summary.md", f1.exists())
        if f1.exists():
            body = f1.read_text(encoding="utf-8")
            check("报告真落内容（含机制名，不是空壳）", "F-GPU-01" in body and len(body) > 200)

        # 成功终态走同一条边（record→escalate）也必须不出"未成"报告
        nodes2 = make_nodes(tmp / "run-ok")
        ok_st = base_state(verdict={"status": "resolved", "target": {"delta": -90.0}},
                           route="done", error=None)
        nodes2["escalate"](ok_st)
        check("resolved 走同一出口 ⇒ 不生成'修复未成'报告（否则撒谎）",
              not (tmp / "run-ok" / "failure_summary.md").exists())
        check("但成功轮照常落 runreport.json（既有行为未被顶掉）",
              (tmp / "run-ok" / "runreport.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n==== 失败总结报告: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
