#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""七节点业务函数端到端离线测试（顶掉所有 Editor/子进程/git 边界，零采样零积分）。

与 test_orchestrator 的分工：那边跑通用桩验图拓扑；这里跑 nodes.py 的
**真实节点函数**，只 mock 边界（_run_detect / resample / gc_floor_measure /
snapmod.* / fixer.execute / verdict_from_bundles），验证节点自身逻辑：
 ① detect：detection.json → findings/Issue；缺产物 → needs_human
 ② diagnose：候选检索 + 排序；候选空 → needs_human（I2 不硬编）
 ③ plan：EditPlan 生成；假设耗尽 → needs_human
 ④ fix：before 束 + 改前态 fresh 在 execute **之前**采集（教训 24 固化）
 ⑤ verify：复用 pre_attempt 的 before，两侧 fresh 对称（教训 17 固化）
 ⑥ review：regressed 自行回滚 + 出口判据不重复回滚
 ⑦ record：写注入的临时知识库（不污染仓库 patterns.db/成功率分母）
 ⑧ escalate：resolved 不回滚 / 未验证 applied 收尾回滚
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import upi.agents.nodes as N          # noqa: E402

# 档案接口在模块顶部导入：setup_mocks 里登记临时工程档案、main 收尾注销。
# 上一版只在 setup_mocks 内局部 import，于是 main 里的 unregister_profile()
# 直接 NameError——断言全绿但退出码非零，"测试通过"与"测试可用"是两件事。
from upi.config.project_profile import (ProjectProfile, register_profile,  # noqa: E402
                                        unregister_profile)

PASS, FAIL = [], []
CALLS = []
TMP = ROOT / "runs" / "_m4w_tmp"
DET = TMP / "det"


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}")


def has(prefix):
    return [c for c in CALLS if c.startswith(prefix)]


def idx(prefix):
    hits = has(prefix)
    return CALLS.index(hits[0]) if hits else -1


def setup_mocks():
    DET.mkdir(parents=True, exist_ok=True)
    # 候选检索必须看到**缺陷态**代码；保留通路跑完后工作区夹具是修复态，
    # 真实检索会返回零候选（那不是 bug，是夹具已被修好）。故把检索根指向
    # 缺陷态参照副本，使本测试与仓库当前状态解耦。
    fx = TMP / "fx" / "F_CPU_01"
    fx.mkdir(parents=True, exist_ok=True)
    (fx / "CombatSkillManager.cs").write_bytes(
        (ROOT / "upi" / "agents" / "_refs" / "F_CPU_01_defect.cs").read_bytes())
    # ⚠️ 阶段 7 换了注入方式。原来写 `CR.FIXTURE_ASSETS = TMP/"fx"`，
    #   profile 化之后那个模块常量已不存在——赋值只会"设一个没人读的属性"，
    #   静默无效，表现为候选检索返回空、端到端三条断言变红，
    #   看着像产品坏了其实是测试写法过期（地基改动让上层写法悄悄失效）。
    #   正确做法：登记一个 project_path 指向临时区的档案，测试与真实接入
    #   （UPI_PROFILE / projects/*.json）走同一入口，不会出现
    #   "测试能注入、生产注入不了"的假通过。
    #   assets_root="fx" ⇒ subject_asset_dir("F-CPU-01") 正好等于上面的 fx/。
    register_profile(ProjectProfile(
        profile_id="m4w-tmp", project_path=TMP, assets_root="fx",
        subjects=["F-CPU-01"], clean_subject="CLEAN",
        subject_file=("fixtures/upi-fixture/Assets/UpiFixtures"
                      "/F_CPU_01/CombatSkillManager.cs"),
        fix_subject="F-CPU-01",
        defect_signature="var ready = new List<SkillRuntime>();"))
    # 原来这里还有 `CR.ROOT = TMP`：profile 化之后候选锚点的基准改成档案
    # git_root，那个模块常量已无消费者——留着就是"设一个没人读的属性"
    # （静默无效，还会误导后人以为改它能换基准）。已删。
    (DET / "detection.json").write_text(json.dumps({
        "results": {"F-CPU-01": {
            "verdict": "detected", "metric": "gc_alloc_per_frame",
            "magnitude": 68576.0, "deviation_ratio": 8.7,
            "detector": "DET-CPU-GC-FRAMEALLOC",
            "issue_signature": {"mechanism": "per_frame_heap_alloc_in_hot_path"},
            "fixture": 77977.0, "clean": 9401.0}}}), encoding="utf-8")

    N._run_detect = lambda: (CALLS.append("run_detect"), DET / "detection.json")[1]

    def fake_resample(fixture, tag, windows=4, timeout_s=1500):
        CALLS.append(f"resample:{tag}")
        p = DET / (tag.replace("/", "_") + ".json")
        w = {"metrics": {"monoUsedBytes": {"median": 105e6},
                         "cpuFrameTimeMs": {"median": 1.9}},
             "a2": {"gc_median": 78000.0, "gc_n": 60}}
        p.write_text(json.dumps({"fixtures": {fixture: {"windows": [w] * 4}}}),
                     encoding="utf-8")
        return {"_path": str(p)}
    N.resample = fake_resample

    def fake_floor(fixture, polls=10, timeout_s=420):
        CALLS.append(f"floor:{fixture}")
        return {"monoUsedBytes": {"pre_gc": 105e6, "post_gc": 104.2e6}}
    N.gc_floor_measure = fake_floor

    class FakeSnap:
        def __init__(self, tag):
            self.git_ref, self.root = f"upi/attempt-{tag}", str(ROOT)

        def to_dict(self):   # 字段与 Snapshot dataclass 全量对齐，
            return {"attempt_id": "t", "git_ref": self.git_ref,  # _snap_from 要重建
                    "head_before": "0" * 40, "asset_manifest_hash": "a" * 16,
                    "scene_guid": "filehash:x", "protocol_fingerprint": "fp",
                    "created_at": "now", "root": self.root, "metadata": {}}
    N.snapmod.snapshot = lambda tag, *a, **k: FakeSnap(tag)
    N.snapmod.rollback = lambda snap, *a, **k: (
        CALLS.append("rollback"),
        {"asset_manifest_restored": True, "scene_restored": True,
         "editor_touched": False})[1]

    N.fixer.execute = lambda plan, snap, **k: (
        CALLS.append(f"execute:{plan.playbook_id}"),
        {"status": "applied", "applied": len(plan.diffs),
         "tier": plan.permission_tier})[1]


def make_verdict_stub(status):
    class FakeVerdict:
        def __init__(self, d):
            self._d = d
            self.status = d["status"]
            self.target = d["target"]

        def model_dump(self):
            return dict(self._d)

    def stub(det_id, fixture, before, after, compile_ok=True,
             floor=None, floor_before=None):
        CALLS.append(f"verdict:fb={floor_before is not None},"
                     f"fa={floor is not None}")
        return FakeVerdict({"status": status, "target": {"delta": 68576.0},
                            "guards": []}), \
            {"memory_guards_unevaluated": []}
    return stub


def fresh_state(run_dir):
    return {"run_id": run_dir.name, "fixture": "F-CPU-01",
            "quality_tier": "urp-balanced",
            "budget": {"max_issues": 1, "max_review_rounds": 2,
                       "max_attempts_per_issue": 2, "wallclock_s": 3600,
                       "max_tokens": 10 ** 9},
            "spent": {"wallclock_s": 0.0, "tokens": 0}, "findings": [],
            "issue": None, "hypotheses": [], "edit_plan": None, "snapshot": None,
            "pre_attempt": None, "verdict": None, "review_decision": None,
            "review_reason": None, "attempts": 0, "route": "proceed",
            "records": [], "error": None}


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True, exist_ok=True)
    setup_mocks()
    db = TMP / "patterns.db"
    nodes = N.make_nodes(TMP, live_llm=False,
                         approval_note="单测批准（替身边界）", knowledge_db=db)

    print("== ① detect ==")
    d = nodes["detect"]({"fixture": "F-CPU-01"})
    check("findings≥1 且 Issue 选对夹具",
          len(d.get("findings", [])) >= 1
          and (d.get("issue") or {}).get("fixture") == "F-CPU-01")
    check("Issue 带 detector/magnitude 供下游",
          d["issue"]["detector"] == "DET-CPU-GC-FRAMEALLOC"
          and d["issue"]["magnitude"] == 68576.0)
    N._run_detect = lambda: None
    d2 = nodes["detect"]({"fixture": "F-CPU-01"})
    check("无检测产物 ⇒ needs_human（I2，不空跑）",
          d2.get("route") == "needs_human")
    setup_mocks()   # 恢复

    st = {**fresh_state(TMP), "issue": d["issue"]}

    print("== ② diagnose ==")
    g = nodes["diagnose"](st)
    hs = g.get("hypotheses", [])
    check("Hypothesis 非空且按 confidence 降序", len(hs) >= 1
          and all(hs[i]["confidence"] >= hs[i + 1]["confidence"]
                  for i in range(len(hs) - 1)))
    check("route=proceed（Top1 过门槛）", g["route"] == "proceed")
    orig = N.retrieve_candidates
    N.retrieve_candidates = lambda issue, det: []
    g2 = nodes["diagnose"](st)
    N.retrieve_candidates = orig
    check("候选空 ⇒ needs_human", g2.get("route") == "needs_human")

    print("== ③ plan ==")
    p = nodes["plan"]({**st, "hypotheses": hs})
    ep = p.get("edit_plan") or {}
    check("EditPlan：PB-FULL-REUSE + 2 diff + blocked",
          ep.get("playbook_id") == "PB-FULL-REUSE"
          and len(ep.get("diffs", [])) == 2
          and ep.get("permission_tier") == "blocked")
    p2 = nodes["plan"]({**st, "hypotheses": []})
    check("假设耗尽 ⇒ needs_human", p2.get("route") == "needs_human")

    print("== ④ fix：基线在写入之前锁定（教训 24）==")
    CALLS.clear()
    f = nodes["fix"]({**st, "hypotheses": hs, "edit_plan": ep})
    ei = idx("execute:")
    check("resample 先于 execute", 0 <= idx("resample:") < ei)
    check("floor 先于 execute", 0 <= idx("floor:") < ei)
    check("applied ⇒ fix 不计 attempts（计数移到 verify）+ pre_attempt 带 floor_before",
          "attempts" not in f and f["pre_attempt"]["floor_before"] is not None)
    check("record 带 root_cause（回环重选判据）",
          f["records"][-1]["root_cause"] == hs[0]["root_cause"])

    vst = {**st, "hypotheses": hs, "edit_plan": ep, **f}

    print("== ⑤ verify：复用 pre before + 两侧 fresh（教训 17）==")
    N.verdict_from_bundles = make_verdict_stub("resolved")
    CALLS.clear()
    v = nodes["verify"](vst)
    check("verdict 收到两侧 fresh", "verdict:fb=True,fa=True" in CALLS)
    check("判定产出 ⇒ attempts=1（作废不烧额配对断言）",
          v.get("attempts") == 1)
    # 标签形如 "<run>/after_<stamp>"；before 束绝不重采（复用 pre_attempt 文件）
    check("before 未重采（复用 pre_attempt 文件）",
          len(has("resample:")) == 1 and "before" not in has("resample:")[0])
    check("改前态 fresh 未重采（从 pre_attempt 读）", len(has("floor:")) == 1)

    print("== ⑥⑦ resolved 直通 record + escalate ==")
    rec = nodes["record"]({**vst, "verdict": v["verdict"]})
    import sqlite3
    ok_db = db.exists()
    if ok_db:
        c = sqlite3.connect(str(db)).execute(
            "SELECT COUNT(*) FROM outcomes WHERE state='resolved'").fetchone()[0]
        ok_db = c >= 1
    check("resolved 写注入库（临时 db，不污染 patterns.db）",
          ok_db and (ROOT / "knowledge" / "patterns.db").exists() is not None)
    nodes["escalate"]({**vst, "verdict": v["verdict"], "route": "proceed",
                       "records": rec["records"]})
    r1 = json.loads((TMP / "runreport.json").read_text(encoding="utf-8"))
    check("runreport 落盘 + resolved 不回滚",
          r1.get("verdict_status") == "resolved" and "final_rollback" not in r1)

    print("== ⑥' regressed 链路：review 自滚 + 出口不重复 ==")
    N.verdict_from_bundles = make_verdict_stub("regressed")
    v2 = nodes["verify"](vst)
    CALLS.clear()
    rv = nodes["review"]({**vst, "verdict": v2["verdict"], "records": []})
    check("rollback_and_replan 且 review 自己回滚",
          rv["review_decision"] == "rollback_and_replan" and "rollback" in CALLS)
    p3 = nodes["plan"]({**st, "hypotheses": hs,
                        "records": [{"kind": "fix",
                                     "root_cause": hs[0]["root_cause"]}]})
    check("回环跳过已试假设（换到下一条根因）",
          p3["route"] == "proceed"
          and p3["edit_plan"] is not None)
    CALLS.clear()
    nodes["escalate"]({**vst, "verdict": v2["verdict"], "route": "needs_human",
                       "error": "budget_exceeded(attempts)",
                       "records": [{"kind": "fix",
                                    "execute": {"status": "applied"}}]})
    r2 = json.loads((TMP / "runreport.json").read_text(encoding="utf-8"))
    check("applied 未验证 ⇒ 出口收尾回滚",
          "rollback" in CALLS and "final_rollback" in r2)
    CALLS.clear()
    nodes["escalate"]({**vst, "verdict": v2["verdict"], "route": "needs_human",
                       "records": [{"kind": "fix",
                                    "execute": {"status": "applied"}},
                                   {"kind": "review", "rollback": {"ok": True}}]})
    check("已回滚过 ⇒ 出口不重复回滚", "rollback" not in CALLS)

    print(f"\n==== 七节点端到端(替身边界): {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    print("边界声明：本测试验证节点逻辑与时序，不是判定结论——resample/floor/"
          "verdict 全为替身；判定语义由 test_memory_guard/test_m3_gates 覆盖，"
          "图拓扑由 test_orchestrator 覆盖。")
    unregister_profile("m4w-tmp")   # 撤掉临时工程档案（见 setup_mocks）
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
