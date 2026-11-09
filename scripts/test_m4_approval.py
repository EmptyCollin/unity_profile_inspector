#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M4 人工批准回路测试（零采样零积分，不碰 Editor/git）。

验的是安全性质，不是判定结论：
 A 工单五件套契约
   A1 缺件（无证据/无回滚/无预案）⇒ 构造期拒收
   A2 diff 无证据对应 ⇒ 不予递交（G5 批准前预演）
   A3 auto 档不进人工回路（§3：无需批准也不该来要批准）
   A4 渲染的工单文本含五件套全部小节与真实数值
 B 决定通道
   B1 approve 缺 approver 或 note ⇒ 拒收（批了没说批什么=没批）
   B2 无 decision.json ⇒ load 返回 None，**绝不默认批准**
   B3 decision.json 非法 JSON / 不合格字段 ⇒ None（不猜、不放行）
   B4 reject/defer ⇒ as_approval_note() 返回空（写入通道保持关闭）
 C fix 节点的先报后批闸（真实节点 + mock 边界）
   C1 无批准文本 ⇒ 产出工单 + needs_human，**不采基线不执行**（零成本）
   C2 批准文件到位 ⇒ 批准文本流到 fixer.execute(human_approved=True)
   C3 批准内容为空 ⇒ 视同未批，闸不放行（fixer 侧同规则双保险）
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError                      # noqa: E402
from upi.core import events as E                          # noqa: E402
from upi.core.approval import (ApprovalDecision,          # noqa: E402
                               ApprovalRequest, ApprovalStore, build_request,
                               render_markdown)
from upi.core.models import EditPlan, Evidence, Hypothesis  # noqa: E402

PASS, FAIL = [], []
TMP = ROOT / "runs" / "_m4apr_tmp"
# 本套的负例用例（缺批准人、非法决定值、损坏决定文件）会触发 approval 里的
# decision_invalid / decision_unreadable 留痕。不隔离总线就会把这些测试噪音
# 写进真实 runs/daemon_events.jsonl（实测污染过 35 行）——而测试全绿不会提示
# 这件事，所以隔离放在这里而不是靠每个用例自己记得。
_REAL_EVENTS = E._p_events()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f"  ← {str(detail)[:120]}"))


EV = [Evidence(tool="unity diagnostics + a2 sampler", raw_ref="runs/x/e.json",
               value=77977.0, unit="B/frame", noise_band=1200.0, data_tier="A2")]
PLAN = EditPlan(playbook_id="PB-SHARED-MAT-MPB",
                diffs=[{"file": "a/BatchBreaker.cs", "find": "r.material = new Material(baseMat);",
                        "replace": "r.sharedMaterial = baseMat;", "expect_count": 1,
                        "_why": "逐实例材质改共享（消材质实例，合批恢复）"}],
                invariants_assert=["颜色值与原逐实例写入一致"],
                side_effect="may_affect_perception",
                touched_assets=["a/BatchBreaker.cs"], permission_tier="semi")


def _issue():
    from upi.core.models import Issue, IssueSignature
    return Issue(signature=IssueSignature(dim="drawCalls", metric="drawCalls",
                                          mechanism="per_renderer_material_instantiation",
                                          threshold=30.0),
                 link="gpu", evidence=list(EV), magnitude=62.0,
                 deviation_ratio=2.1, state="confirmed")


def _hyp():
    from upi.core.attribution import FactorJudgment, build_hypothesis
    js = [FactorJudgment(factor=f, value=v, evidence_ref="runs/x/e.json")
          for f, v in (("proximity", 0.9), ("explains_share", 0.8),
                       ("competitiveness", 0.6), ("prior_hit", 0.5),
                       ("counterfactual", 0.7), ("mechanism", 0.9))]
    return build_hypothesis("逐渲染器实例化材质破坏 SRP Batcher",
                            ["a/BatchBreaker.cs:38"], js, _issue())


def req_kwargs(**over):
    kw = dict(run_id="r1", issue=_issue(), hypothesis=_hyp(), plan=PLAN,
              permission_tier="semi", reason_not_auto="触碰材质=感知层（§3 维度一）",
              git_ref="upi/attempt-r1")
    kw.update(over)
    return build_request(**kw)


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    run_dir = TMP / "run1"
    run_dir.mkdir(parents=True)
    # 总线隔离到本套自己的临时目录（rmtree 时一并消失），并记下真实事件档
    # 的初始大小，收尾断言它没被写过——污染仓库留档不会让测试变红，
    # 只能靠这样一条自检守住。
    E.isolate(TMP)
    _size0 = _REAL_EVENTS.stat().st_size if _REAL_EVENTS.exists() else 0

    print("== A 工单五件套契约 ==")
    req = req_kwargs()
    check("合法工单构造成功", isinstance(req, ApprovalRequest))
    for field, why in (("evidence", "无证据"), ("rollback", "无回滚方式"),
                       ("failure_plan", "无失败预案")):
        bad = req.model_dump()
        bad.pop(field)
        try:
            ApprovalRequest(**bad)
            ok = False
        except ValidationError:
            ok = True
        check(f"{why} ⇒ 构造期拒收", ok)
    d_mapped = req.model_dump()
    d_mapped["diff_evidence_map"][0]["evidence_refs"] = []
    try:
        ApprovalRequest(**d_mapped)
        ok = False
    except ValidationError as e:
        ok = "G5" in str(e) or "不予递交" in str(e)
    check("diff 指不到证据 ⇒ 不予递交（G5 批准前预演）", ok)
    try:
        req_kwargs(permission_tier="auto")
        ok = False
    except (ValidationError, ValueError):
        ok = True
    check("auto 档不进人工回路", ok)

    md = render_markdown(req)
    need = ["待批准工单", "1 证据", "2 方案", "3 预期效应", "4 回滚方式",
            "5 失败预案", "77977", "upi/attempt-r1", "为什么不是你自动就能落地"]
    check("工单文本含五件套全部小节 + 真实数值 + tag",
          all(k in md for k in need))
    check("工单不含凭据类内容（I12）", "api_key" not in md.lower()
          and "sk-" not in md)

    print("== B 决定通道 ==")
    store = ApprovalStore(run_dir)
    where = store.submit(req)
    rid = req.request_id
    check("工单落 request.json + request.md",
          Path(where["md"]).exists() and (store._dir(rid) / "request.json").exists())
    check("待批队列可见", rid in store.list_open())
    check("无 decision.json ⇒ None（绝不默认批准）", store.load_decision(rid) is None)

    for raw, why in (('{"decision": "approve", "approver": "", "note": ""}',
                      "approve 无批准人无内容"),
                     ('{"decision": "approve", "approver": "张三"}',
                      "approve 有批人没批内容"),
                     ('not json at all', "非法 JSON"),
                     ('{"decision": "maybe", "approver": "张三", "note": "x"}',
                      "决定值不合法")):
        (store._dir(rid) / "decision.json").write_text(raw, encoding="utf-8")
        check(f"{why} ⇒ 无有效决定（不放行写入）", store.load_decision(rid) is None)

    (store._dir(rid) / "decision.json").write_text(json.dumps(
        {"decision": "reject", "approver": "李四", "note": "材质改动需美术确认"},
        ensure_ascii=False), encoding="utf-8")
    rej = store.load_decision(rid)
    check("reject 可读出且批准文本为空（写入通道保持关闭）",
          rej is not None and rej.as_approval_note() == "")

    (store._dir(rid) / "decision.json").write_text(json.dumps(
        {"decision": "approve", "approver": "王五", "note": "批准 MPB 方案，颜色须一致"},
        ensure_ascii=False), encoding="utf-8")
    app = store.load_decision(rid)
    note = app.as_approval_note()
    check("approve ⇒ 批准文本含批准人与批的内容（可回溯）",
          "王五" in note and "MPB" in note and rid in note)
    store.record_used(rid, app, {"stage": "granted"})
    check("决定消费后落 used.json（§8.5 分层统计原料）",
          (store._dir(rid) / "used.json").exists())
    check("已批工单不再出现在待批队列", rid not in store.list_open())

    print("== C fix 节点的先报后批闸（真实节点，边界全 mock）==")
    import upi.agents.nodes as N
    calls = []
    N._run_detect = lambda: None
    N.resample = lambda *a, **k: calls.append("resample") or {"_path": "x"}
    N.gc_floor_measure = lambda *a, **k: calls.append("floor") or {}
    N.fixer.execute = lambda plan, snap, **k: (
        calls.append(f"execute:approved={k.get('human_approved')}"),
        {"status": "applied"})[1]

    class FakeSnap:
        def __init__(self, tag):
            self.git_ref = f"upi/attempt-{tag}"

        def to_dict(self):
            return {"git_ref": self.git_ref, "root": str(ROOT)}
    N.snapmod.snapshot = lambda tag, *a, **k: FakeSnap(tag)
    N.snapmod.rollback = lambda *a, **k: calls.append("rollback") or {"ok": True}

    st = {"issue": {"id": "ISS-1", "fixture": "F-CPU-01",
                    "detector": "DET-CPU-GC-FRAMEALLOC",
                    "metric": "gc_alloc_per_frame", "magnitude": 68576.0,
                    "evidence": {"fixture": 77977.0, "clean": 9401.0,
                                 "deviation_ratio": 8.7},
                    "state": "confirmed"},
           "hypotheses": [{"root_cause": "热路径每帧新建 List（可复用缓冲）",
                           "code_anchors": ["x.cs:21"],
                           "attribution_factors": {}, "confidence": 0.9,
                           "weights_version": "hand-set-prior-v1",
                           "expected_effect": {"metric": "gc_alloc_per_frame",
                                               "direction": "down",
                                               "min_delta": 20000},
                           "veto_reasons": []}],
           "edit_plan": PLAN.model_dump(), "records": [], "attempts": 0}

    # C1：没批准文本 ⇒ 出工单、零采样、needs_human
    n_nolimit = N.make_nodes(run_dir, live_llm=False, approval_note="")
    calls.clear()
    f1 = n_nolimit["fix"](dict(st))
    check("无批准 ⇒ 不采基线不执行（零采样成本）",
          "resample" not in calls and "floor" not in calls
          and not any(c.startswith("execute") for c in calls))
    check("无批准 ⇒ needs_human 且记录 awaiting_approval",
          f1.get("route") == "needs_human"
          and f1["records"][-1]["execute"]["status"] == "awaiting_approval")
    rid2 = f1["approval_request"]
    check("工单文件已产出（人可读 md）",
          Path(rid2["md"]).exists() and "5 失败预案" in Path(rid2["md"]).read_text(encoding="utf-8")
          .replace("## 5 失败预案", "5 失败预案"))

    # C2：人写了批准 ⇒ 文本流到 fixer
    d2 = json.loads((Path(rid2["md"]).parent / "request.json").read_text(encoding="utf-8"))
    (Path(rid2["md"]).parent / "decision.json").write_text(json.dumps(
        {"decision": "approve", "approver": "测试批准人",
         "note": "批准预生成方案，须核对语义等价"}, ensure_ascii=False),
        encoding="utf-8")
    calls.clear()
    f2 = n_nolimit["fix"]({**st, "records": []})
    ex = [c for c in calls if c.startswith("execute:")]
    check("有批准 ⇒ 采基线并执行，且 human_approved=True",
          "resample" in calls and ex == ["execute:approved=True"])
    check("批准文本进了 fixer 调用（可回溯到批准人）",
          f2["records"][-1]["execute"].get("status") == "applied")

    # C3：批准文件写了但内容空 ⇒ 闸不放行
    (Path(rid2["md"]).parent / "decision.json").write_text(json.dumps(
        {"decision": "approve", "approver": "测试批准人", "note": "   "},
        ensure_ascii=False), encoding="utf-8")
    calls.clear()
    f3 = n_nolimit["fix"]({**st, "records": []})
    check("批准内容为空 ⇒ 视同未批，不执行（双保险）",
          not any(c.startswith("execute") for c in calls)
          and f3.get("route") == "needs_human")

    # 自检：真实事件档不得被本套写过。这条断言存在的意义是——测试全绿
    # 不代表没污染仓库：负例留痕一旦写到 runs/daemon_events.jsonl，
    # 会混进报告的事件流与统计，而没人会怀疑是测试干的。
    _size1 = _REAL_EVENTS.stat().st_size if _REAL_EVENTS.exists() else 0
    check("真实事件档未被本套污染（负例留痕不入库）",
          _size1 == _size0, f"size {_size0} -> {_size1}")

    print(f"\n==== M4 人工回路测试: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
