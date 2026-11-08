#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态机图路由离线测试（不碰 Editor 不烧积分，验证的是"电路"不是"判定"）。

被测对象（orchestrator.build_orchestrator + _wrap 包装器）：
 ① happy path：detect→diagnose→plan→fix→verify(resolved)→record→escalate→END
 ② diagnose needs_human → 直接 escalate（不进 fix）
 ③ verify regressed → review → rollback_and_replan → diagnose（回环）
 ④ verify not_significant → review → continue_with_direction → plan（回环）
 ⑤ G11 预算：attempts 耗尽 → 节点入口拦停 → needs_human
 ⑥ 节点抛异常 → 成状态不炸图，span exit_status=1
 ⑦ 每节点一个 span（I10）+ 每节点退出落盘 state.json
 ⑧ budget_check 的 tokens 分支
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.agents.orchestrator import build_orchestrator, budget_check  # noqa: E402
from upi.tooling.trace import Tracer                                  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}")


def run_case(verdict_status="resolved", budget=None, route_at=None,
             raise_at=None):
    """stub 节点跑图。节点动作 = dict 或 fn(st)->dict；统一记录 visited。"""
    tmp = Path(tempfile.mkdtemp(prefix="upi_orch_"))
    visited = []

    def node(nname, act):
        def fn(st):
            visited.append(nname)
            if raise_at == nname:
                raise RuntimeError(f"stub 故障@{nname}")
            d = act(st) if callable(act) else act
            return dict(d)
        return fn

    lim = (budget or {}).get("max_review_rounds", 3)

    def review_fn(st):
        n = visited.count("review")   # node() 已 append 本次，不再 +1
        if verdict_status == "regressed":
            dec = "rollback_and_replan" if n < lim else "escalate_human"
        elif verdict_status == "not_significant":
            dec = "continue_with_direction" if n < lim else "escalate_human"
        else:
            dec = "escalate_human"
        return {"review_decision": dec}

    base = {"issue": {"id": "ISS-1", "fixture": "F-CPU-01", "detector": "DET-X",
                      "metric": "gc_alloc_per_frame", "evidence": {}}}
    acts = {
        "detect": base,
        "diagnose": {"hypotheses": [{"root_cause": "rc-A", "confidence": 0.9},
                                    {"root_cause": "rc-B", "confidence": 0.7}]},
        "plan": {"edit_plan": {"playbook_id": "PB", "diffs": []}},
        # attempts 由状态真实累计（G11 判据吃它）
        "fix": lambda st: {"snapshot": {"git_ref": "upi/attempt-t",
                                        "root": str(ROOT)},
                           "pre_attempt": {"stamp": "t", "before_path": "x",
                                           "floor_before": None,
                                           "floor_before_err": None},
                           "attempts": st.get("attempts", 0) + 1,
                           "records": st.get("records", []) + [
                               {"kind": "fix",
                                "root_cause": st["hypotheses"][0]["root_cause"]}]},
        "verify": {"verdict": {"status": verdict_status}},
        "review": review_fn,
        "record": {},
        "escalate": {},
    }
    if route_at:
        extra = {"route": "needs_human", "error": f"stub@{route_at}"}
        cur = acts[route_at]
        acts[route_at] = (lambda c, e: (lambda st: {**(c(st) if callable(c) else c), **e}))(cur, extra)

    tracer = Tracer("t-" + verdict_status + (route_at or "") + (raise_at or ""),
                    root=str(tmp), channel="dev", env_fingerprint="test")
    nodes = {n: node(n, a) for n, a in acts.items()}
    app = build_orchestrator(nodes, tracer)
    st0 = {"run_id": tracer.run_id, "fixture": "F-CPU-01",
           "quality_tier": "urp-balanced",
           "budget": {"max_review_rounds": 3, "max_attempts_per_issue": 3,
                      "wallclock_s": 2400, "max_tokens": 60_000, **(budget or {})},
           "spent": {"wallclock_s": 0.0, "tokens": 0}, "findings": [], "issue": None,
           "hypotheses": [], "edit_plan": None, "snapshot": None, "pre_attempt": None,
           "verdict": None, "review_decision": None, "review_reason": None,
           "attempts": 0, "route": "proceed", "records": [], "error": None}
    final = app.invoke(st0)
    spans = [json.loads(l) for l in tracer.path.read_text(encoding="utf-8").splitlines()]
    out = {"final": final, "visited": visited, "spans": spans,
           "state_exists": (tracer.dir / "state.json").exists(), "dir": tmp}
    return out



def main():
    print("== ① happy path resolved ==")
    c = run_case("resolved")
    v = c["visited"]
    check("节点顺序 detect→diagnose→plan→fix→verify→record→escalate",
          v == ["detect", "diagnose", "plan", "fix", "verify", "record", "escalate"])
    check("resolved 不进 review", "review" not in v)
    check("每节点一个 span（7 节点 7 span）", len(c["spans"]) == 7)
    check("span 含 run_id/stage/exit_status/wallclock_ms", all(
        {"run_id", "stage", "exit_status", "wallclock_ms"} <= set(s) for s in c["spans"]))
    check("state.json 已落盘", c["state_exists"])
    check("route 无 needs_human", c["final"].get("route") != "needs_human")

    print("== ② diagnose needs_human → 直接 escalate ==")
    c2 = run_case(route_at="diagnose")
    check("未进 fix/verify", "fix" not in c2["visited"] and "verify" not in c2["visited"])
    check("走了 escalate", "escalate" in c2["visited"])
    check("stub 的 error 文本保留", "stub@diagnose" in str(c2["final"].get("error", "")))

    print("== ③ verify regressed → review 回环；两条停止路径都收敛 escalate ==")
    # 节点感知预算闸（批准后链路实测撞出的语义修正）：attempts 只拦 fix 入口，
    # 默认预算(3,3)下 regressed 回环跑满 3 次 attempt，verify 每次都执行
    # （不白烧采样），出环由 review 配额 n<lim 判 escalate_human。
    c3 = run_case("regressed")
    v3 = c3["visited"]
    check("review 被执行", "review" in v3)
    # 回环确实发生：第一次 review 之后又跑了 diagnose（从 review 位置起搜，
    # 不能用 index("diagnose",1)——那从索引1起搜先撞上第一轮的 diagnose）
    check("回环：review 之后又跑 diagnose",
          "diagnose" in v3[v3.index("review") + 1:])
    check("attempts 满不拦 verify：verify 跑满3、review 由配额出环（旧误拦已修）",
          c3["final"].get("attempts") == 3 and v3.count("verify") == 3
          and v3.count("review") == 3 and v3[-1] == "escalate")
    check("不进 record（未成功）", "record" not in v3)
    # 放宽 attempts 后 review 配额单独触发（G6：3 次审查耗尽 → 出环）
    c3b = run_case("regressed", budget={"max_attempts_per_issue": 99})
    v3b = c3b["visited"]
    check("review 配额单独生效：3 次审查后 escalate_human 出环",
          v3b.count("review") == 3 and v3b[-1] == "escalate"
          and v3b[-2] == "review")

    print("== ④ not_significant → continue_with_direction 回 plan ==")
    c4 = run_case("not_significant")
    v4 = c4["visited"]
    check("plan 被执行 ≥2 次（回环）", v4.count("plan") >= 2)
    check("最终 escalate 出环", v4[-1] == "escalate")

    print("== ⑤ G11 attempts 耗尽 → 节点入口拦停 ==")
    c5 = run_case("not_significant", budget={"max_attempts_per_issue": 2,
                                             "max_review_rounds": 9})
    check("error=budget_exceeded(attempts)",
          str(c5["final"].get("error", "")).startswith("budget_exceeded(attempts)"))
    check("attempts 恰停在 2", c5["final"].get("attempts") == 2)

    print("== ⑥ 节点抛异常 → 成状态不炸图 ==")
    c6 = run_case(raise_at="fix")
    check("异常成 needs_human 状态", c6["final"].get("route") == "needs_human"
          and "stub 故障" in str(c6["final"].get("error", "")))
    crash_spans = [s for s in c6["spans"] if s["stage"] == "fix"]
    check("故障节点 span exit_status=1", bool(crash_spans)
          and crash_spans[0]["exit_status"] == 1)

    print("== ⑥b _needs_final_rollback 出口判据（纯函数）==")
    from upi.agents.nodes import _needs_final_rollback as need
    fixd = [{"kind": "fix", "execute": {"status": "applied"}}]
    check("applied 且无后续回滚且未 resolved ⇒ 需收尾回滚", need(fixd, None) is True)
    check("resolved ⇒ 不回滚（改动要保留）", need(fixd, "resolved") is False)
    applied_then_rb = fixd + [{"kind": "review", "rollback": {"ok": True}}]
    check("review 已回滚 ⇒ 不重复回滚", need(applied_then_rb, None) is False)
    check("最后 fix 未 applied（编译失败已回滚）⇒ 无需再滚",
          need([{"kind": "fix", "execute": {"status": "compile_failed_rolled_back"}}],
               None) is False)
    check("无 fix 记录 ⇒ 无需回滚", need([{"kind": "detect"}], None) is False)
    two_fix = [{"kind": "fix", "execute": {"status": "applied"}},
               {"kind": "review", "rollback": {"ok": True}},
               {"kind": "fix", "execute": {"status": "applied"}}]
    check("第二次 fix 无回滚 ⇒ 需收尾（只看最后一次 fix 之后）",
          need(two_fix, None) is True)

    print("== ⑥c fix 早退直达出口（patrol-once 实测撞出的路由缺口）==")
    # awaiting_approval / rejected / diff_rejected / compile_failed 四类早退
    # 都返回 route=needs_human——无条件 fix→verify 边会让 verify 照跑，
    # 读不到 pre_attempt 崩 KeyError，把干净的"待批"终态污染成假错误。
    c9 = run_case(route_at="fix")
    v9 = c9["visited"]
    check("fix needs_human ⇒ verify 未跑、直达 escalate",
          "verify" not in v9 and v9[-2:] == ["fix", "escalate"])

    print("== ⑦ budget_check 纯函数分支 ==")
    check("tokens 超限命中", budget_check(
        {"budget": {"max_tokens": 100}, "spent": {"tokens": 150}}
    ).startswith("budget_exceeded(tokens)"))
    check("wallclock 超限命中", budget_check(
        {"budget": {"wallclock_s": 10}, "spent": {"wallclock_s": 11}}
    ).startswith("budget_exceeded(wallclock)"))
    check("未超限放行", budget_check(
        {"budget": {"max_tokens": 100, "wallclock_s": 10},
         "spent": {"tokens": 50, "wallclock_s": 1}}) is None)

    for c_ in (c, c2, c3, c3b, c4, c5, c6):
        shutil.rmtree(c_["dir"], ignore_errors=True)

    print(f"\n==== 状态机路由测试: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
