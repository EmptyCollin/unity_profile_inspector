"""七节点业务实现（喂给 orchestrator.build_orchestrator 的 nodes）。

设计原则：节点只做"取数 + 调既有实测函数 + 填 RunState 增量"，
判定权全在下面的既有模块（detector/attribution/fixer/verify）——
节点本身不产生任何数值或结论（I1）。每个节点返回 dict 增量，
由 orchestrator 的包装器合并、计预算、落 span、写 state.json。

与彩排脚本的差别：彩排是一条直线（步骤写死），这里是状态机——
diagnose 出 needs_human 直接出口、verify 不 resolve 进 review、
review 可回 plan 或 diagnose，配额耗尽由 G11 预算闸拦停。

真实 LLM 归因（diagnose 节点）受 I13 约束：连通性门禁在 factor_judge
内部（不通过即拒发请求），本模块不绕过它；端到端演练是否跑真模型
由脚本层的 --live-llm 开关决定，默认离线替身，不擅自烧积分。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]

from upi.agents import fixer
from upi.agents.verify import (gc_floor_measure, resample, verdict_from_bundles)
from upi.config.settings import load_config
from upi.core.candidate_retrieval import retrieve_candidates
from upi.core.detector import DEFAULT_REGISTRY
from upi.core.models import EditPlan, Issue
from upi.tooling import snapshot as snapmod

PROJECT = ROOT / "fixtures" / "upi-fixture"
SCENE = PROJECT / "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity"
CSFILE = "fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs"

# anchor 机制 → 该机制对应的语义保持修复（PB-* 取自 DET-CPU-GC-FRAMEALLOC.playbooks，
# 均 whitelisted / side_effect=none）。diff 模板与彩排同源，逐条对应证据。
_T = (ROOT / CSFILE).read_text(encoding="utf-8").split("\n")
_lines = lambda a, b: "\n".join(_T[a - 1:b])


def _plan_for(anchor: str) -> Optional[EditPlan]:
    """把选定根因机制映射到 EditPlan（找不到模板 = 不硬编，交上层 needs_human）。"""
    tpl = PLAYBOOK_DIFFS.get(anchor)
    if tpl is None:
        return None
    pid, diffs, invs = tpl
    return EditPlan(playbook_id=pid, diffs=diffs, invariants_assert=invs,
                    side_effect="none", touched_assets=[CSFILE],
                    permission_tier="blocked")   # 夹具脚本恒 blocked（§3 维度一）


# 复用彩排的三段 diff（Awake 预生成 + Update 分配清零），按主锚点归一个 playbook
PB = "PB-FULL-REUSE"
_NEW_AWAKE = '''        private readonly List<SkillRuntime> _skills = new List<SkillRuntime>();

        // 修复（PB-FULL-REUSE）：缓存 + 预生成。语义等价前提——state/level
        // 在 Awake 后不再被改写（Update 全文无赋值点，已核对），故 ready 筛选、
        // 排序结果、key 字符串均为常量，可一次预生成。
        private Rigidbody _body;
        private CapsuleCollider _col;
        private List<SkillRuntime> _readyRanked;
        private string[] _keyByIndex;
        private readonly Dictionary<int, float> _reusedCooldown = new Dictionary<int, float>();

        private void Awake()
        {
            for (int i = 0; i < skillCount; i++)
                _skills.Add(new SkillRuntime { index = i, level = 1 + (i % 3), state = (SkillState)(i % 4) });

            _body = GetComponent<Rigidbody>();
            _col = GetComponent<CapsuleCollider>();
            _readyRanked = _skills.Where(x => x.state == SkillState.Ready)
                                   .Where(x => x.level > 1)
                                   .OrderByDescending(x => x.level).ThenBy(x => x.index).ToList();
            _keyByIndex = new string[skillCount];
            foreach (var s in _skills)
                _keyByIndex[s.index] = s.index + ":" + s.state + ":"
                    + (_body != null ? 1 : 0) + (_col != null ? 1 : 0);
        }'''

_NEW_UPDATE = '''            // 每帧分配清零：
            foreach (var s in _readyRanked)
            {
                var key = _keyByIndex[s.index];
                s.lastKey = key;
                s.score = s.level;
            }

            _reusedCooldown.Clear();
            for (int i = 0; i < _skills.Count; i++)
                _reusedCooldown[i] = Time.time + 0.1f * i;
            _cooldownMap = _reusedCooldown;'''

_D1 = {"file": CSFILE, "find": _lines(21, 27), "replace": _NEW_AWAKE,
       "expect_count": 1, "_why": "缓冲/组件/键 预生成（Awake 扩展）"}
_D2 = {"file": CSFILE, "find": _lines(31, 57), "replace": _NEW_UPDATE,
       "expect_count": 1, "_why": "Update 热路径分配清零"}

# 四个 CPU 机制锚点共享同一套 full-reuse 模板（夹具就是这四类叠加）
_INVS = ["state/level 仅 Awake 赋值（静态核对）",
         "map[key]=level;score=map[key] ⇒ score=level 恒等",
         "_cooldownMap 声明保留（对外语义不破坏），改指向复用缓冲",
         "P2 语义断言基建 pending（M7）——本条如实标注"]
PLAYBOOK_DIFFS = {a: (PB, [_D1, _D2], _INVS) for a in (
    "per_frame_heap_alloc_in_hot_path", "linq_enumerator_closure_per_frame",
    "repeated_getcomponent_in_update", "enum_tostring_concat_per_frame")}


def make_nodes(run_dir: Path, *, live_llm: bool = False,
               approval_note: str = "") -> dict:
    """构造七节点函数表。live_llm=False 时 diagnose 用离线替身（不烧积分）。"""

    def detect(st: dict) -> dict:
        # 复用 m1_detect 的判据链，但只取"当前夹具"这一条 Issue 进循环
        import subprocess, sys
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "m1_detect.py")],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        # 定位本轮 detection.json：stdout 末行 "trace: <abs>/trace.jsonl" 是唯一
        # 稳定指针（m1_detect 不落"最新"软链，也不该为它加副作用）
        det_fp = None
        for ln in p.stdout.splitlines():
            if ln.startswith("trace:"):
                det_fp = Path(ln.split(":", 1)[1].strip()).parent / "detection.json"
                break
        findings = []
        issue = None
        if det_fp and det_fp.exists():
            dj = json.loads(det_fp.read_text(encoding="utf-8"))
            for fid, r in dj.get("results", {}).items():
                if r.get("verdict") == "detected":
                    sig = r.get("issue_signature", {})
                    findings.append({
                        "kind": "issue", "fixture": fid, "metric": r.get("metric"),
                        "magnitude": r.get("magnitude"),
                        "detector": r.get("detector"),
                        "signature": sig})
                    if fid == st["fixture"] and issue is None:
                        issue = {"id": f"ISS-{fid}", "fixture": fid,
                                 "detector": r.get("detector"),
                                 "metric": r.get("metric"),
                                 "magnitude": r.get("magnitude"),
                                 "evidence": r, "state": "confirmed",
                                 "anchor": sig.get("mechanism")}
        if issue is None:
            return {"route": "needs_human",
                    "error": f"detect 未在当前夹具检出 Issue（m1_detect rc={p.returncode}）"}
        return {"findings": findings, "issue": issue, "route": "proceed"}

    def diagnose(st: dict) -> dict:
        issue_d = st["issue"]
        det = next(d for d in DEFAULT_REGISTRY if d.id == issue_d["detector"])
        issue = Issue(**issue_d["issue_model"]) if "issue_model" in issue_d else _rebuild_issue(det, issue_d)
        cands = retrieve_candidates(issue, det)
        if not cands:
            return {"route": "needs_human", "error": "候选检索为空（证据不足，I2）"}
        if live_llm:
            from upi.agents.factor_judge import build_hypotheses_for_issue
            cfg = load_config(ROOT / "llm_config.yaml")
            hs, archive = build_hypotheses_for_issue(cfg, issue, cands)
            tokens = sum((a.get("_meta", {}).get("token_in", 0) or 0)
                         + (a.get("_meta", {}).get("token_out", 0) or 0)
                         for a in archive.values())
            (run_dir / "diagnose_archive.json").write_text(
                json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            hs = _offline_hypotheses(cands, issue)
            tokens = 0
        from upi.core.attribution import route as attr_route
        decision = attr_route(hs)
        if decision == "needs_human":
            return {"hypotheses": [h.model_dump() for h in hs], "route": "needs_human",
                    "error": f"Top1 归因置信度低于门槛（attr_route={decision}）",
                    "tokens_used": tokens}
        return {"hypotheses": [h.model_dump() for h in hs], "route": "proceed",
                "tokens_used": tokens}

    def plan(st: dict) -> dict:
        # continue_with_direction 回环时跳过已 attempt 过的根因——重选假设，
        # 而不是对着同一条路再撞一次（§1.6 reason 回传给 Fix 的最小落实）
        tried = {r.get("root_cause") for r in st.get("records", [])
                 if r.get("kind") == "fix"}
        for top in st["hypotheses"]:
            if top["root_cause"] in tried:
                continue
            ep = _plan_for(_anchor_of(top["root_cause"]))
            if ep is not None:
                return {"edit_plan": ep.model_dump(), "route": "proceed"}
        return {"route": "needs_human",
                "error": f"候选假设已穷尽（{len(tried)} 条试过），转人工"}

    def fix(st: dict) -> dict:
        ep = EditPlan(**st["edit_plan"])
        fp = "warmup=8,window=20,poll=1.0,resident=breachprobe+replaydriver+a2,replay=none-v0"
        stamp = time.strftime("%H%M%S")
        snap = snapmod.snapshot(f"m4-{stamp}", PROJECT, SCENE, fp, root=ROOT)
        fixture = st["issue"]["fixture"]

        # 时序铁律（M4 接线自查抓出）：attempt 前基线必须在**写入之前**锁定。
        # verify 节点跑在 fix 之后，届时夹具已是修复态——再采"before"
        # 等于拿修复态数据冒充改前，两侧对比直接失真。before 束与改前态
        # fresh 都在此刻（已快照、未 execute、仍是缺陷态）采集。
        before = resample(fixture, f"{run_dir.name}/pre_attempt_{stamp}")
        floor_before, fb_err = None, None
        try:
            floor_before = gc_floor_measure(fixture)
        except Exception as e:  # noqa: BLE001 — 缺侧即盲区，fail-open 到未评估
            fb_err = str(e)[:160]

        rep = fixer.execute(ep, snap, project_path=PROJECT, scene_path=SCENE,
                            root=ROOT, human_approved=bool(approval_note),
                            approval_note=approval_note)
        rec = {"kind": "fix", "ts": stamp, "git_ref": snap.git_ref,
               "root_cause": st["hypotheses"][0]["root_cause"],
               "snapshot": snap.to_dict(), "execute": rep}
        if rep["status"] != "applied":
            return {"snapshot": snap.to_dict(), "route": "needs_human",
                    "error": f"fix {rep['status']}: {rep.get('why','')[:120]}",
                    "records": st.get("records", []) + [rec]}
        pre = {"stamp": stamp, "before_path": before["_path"],
               "floor_before": floor_before, "floor_before_err": fb_err}
        return {"snapshot": snap.to_dict(), "pre_attempt": pre,
                "attempts": st.get("attempts", 0) + 1,
                "records": st.get("records", []) + [rec], "route": "proceed"}

    def verify(st: dict) -> dict:
        pre = st["pre_attempt"]                  # fix 节点写入前锁定的基线
        stamp = pre["stamp"]
        before = json.loads(Path(pre["before_path"]).read_text(encoding="utf-8"))
        before["_path"] = pre["before_path"]
        after = resample(st["issue"]["fixture"], f"{run_dir.name}/after_{stamp}")
        floor = None
        try:
            floor = gc_floor_measure(st["issue"]["fixture"])
        except Exception:  # noqa: BLE001 — 缺侧即盲区
            floor = None
        v, detail = verdict_from_bundles(st["issue"]["detector"], st["issue"]["fixture"],
                                         before, after, compile_ok=True,
                                         floor=floor,
                                         floor_before=pre["floor_before"])
        rec = {"kind": "verify", "status": v.status,
               "target": v.target, "unevaluated": detail.get("memory_guards_unevaluated", []),
               "floor_before_err": pre["floor_before_err"]}
        return {"verdict": v.model_dump(),
                "records": st.get("records", []) + [rec], "route": "proceed"}

    def review(st: dict) -> dict:
        # 确定性裁定（§1.6 信息隔离：只吃 Verdict+diff+全量数据，不吃 Fix 自辩）
        v = st["verdict"]
        status = v["status"] if isinstance(v, dict) else v.status
        n_review = len([r for r in st.get("records", []) if r.get("kind") == "review"]) + 1
        rec = {"kind": "review", "round": n_review, "verdict_status": status}
        if status == "regressed":
            # 引入新问题 → 撤回重评（回滚交 git，review 不自己改文件）
            rb = snapmod.rollback(_snap_from(st), PROJECT, SCENE)
            rec["rollback"] = rb
            decision = "rollback_and_replan" if n_review < st["budget"]["max_review_rounds"] \
                else "escalate_human"
        elif status == "not_significant":
            decision = "continue_with_direction" if n_review < st["budget"]["max_review_rounds"] \
                else "escalate_human"
        else:
            decision = "escalate_human"
        return {"review_decision": decision,
                "review_reason": f"verdict={status} round={n_review}",
                "records": st.get("records", []) + [rec], "route": "proceed"}

    def record(st: dict) -> dict:
        # 成功修复才写知识库（症状×根因×动作三重签名）
        from upi.core.knowledge import KnowledgeStore
        ks = KnowledgeStore(ROOT / "knowledge" / "patterns.db")
        try:
            top = st["hypotheses"][0]
            pid = _pattern_id(st["issue"], top)
            ks.upsert_pattern(pid, st["issue"]["metric"], "cpu",
                              top["root_cause"], top.get("playbook_id", PB),
                              playbook_id=top.get("playbook_id", PB))
            v = st["verdict"]
            st_status = v["status"] if isinstance(v, dict) else v.status
            ks.record_outcome(pid, run_dir.name, st["issue"]["fixture"],
                              st["quality_tier"], st_status,
                              metric_delta=(v.get("target", {}).get("delta")
                                            if isinstance(v, dict) else None))
        finally:
            ks.close()
        return {"records": st.get("records", []) + [{"kind": "record"}]}

    def escalate(st: dict) -> dict:
        final = {"run_id": run_dir.name, "fixture": st.get("fixture"),
                 "route": st.get("route"), "error": st.get("error"),
                 "attempts": st.get("attempts", 0), "spent": st.get("spent")}
        v = st.get("verdict")
        if v:
            final["verdict_status"] = v["status"] if isinstance(v, dict) else v.status
        (run_dir / "runreport.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        return {}

    return {"detect": detect, "diagnose": diagnose, "plan": plan, "fix": fix,
            "verify": verify, "review": review, "record": record,
            "escalate": escalate}


# ---------- 小工具：把 detect 的 result dict 还原成 Issue 契约对象 ----------
def _rebuild_issue(det, issue_d: dict) -> Issue:
    from upi.core.models import Evidence, IssueSignature
    r = issue_d["evidence"]
    sig = IssueSignature(dim=det.metric, metric=det.metric,
                         mechanism=det.anchor_patterns[0],
                         threshold=det.thresholds.min_magnitude)
    ev = Evidence(tool="unity diagnostics + a2 sampler (m1_detect)",
                  raw_ref="runs/_last_run", value=float(r["fixture"]),
                  unit=det.unit, noise_band=0.0, data_tier=det.source_tier)
    return Issue(signature=sig, link=det.link, evidence=[ev],
                 magnitude=float(r.get("magnitude", 0) or 0),
                 deviation_ratio=float(r.get("deviation_ratio", 0) or 0),
                 explains_share_of_regression=1.0, state="confirmed",
                 low_confidence=False)


def _offline_hypotheses(cands, issue) -> list:
    """离线替身：不调用 LLM，用候选检索的结构化命中直接构造 FactorJudgment
    （每个 anchor 命中即该因子 likely）——用于在没有真推理的情况下把状态机
    流程跑通。真实归因质量必须靠 --live-llm 验证，替身不产出对外置信度结论。"""
    from upi.core.attribution import FactorJudgment, build_hypothesis
    hs = []
    for root_cause, anchors in cands:
        js = [FactorJudgment(factor="proximity", value=0.7,
                             evidence_ref="anchor:" + (anchors[0] if anchors else "")),
              FactorJudgment(factor="explains_share", value=0.6, evidence_ref="e0"),
              FactorJudgment(factor="mechanism", value=0.8, evidence_ref="e0"),
              FactorJudgment(factor="competitiveness", value=0.5, evidence_ref="e0"),
              FactorJudgment(factor="prior_hit", value=0.5, evidence_ref="e0"),
              FactorJudgment(factor="counterfactual", value=0.5, evidence_ref="e0")]
        hs.append(build_hypothesis(root_cause, anchors, js, issue))
    from upi.core.attribution import rank
    return rank(hs)


def _anchor_of(root_cause: str) -> str:
    for a in PLAYBOOK_DIFFS:
        if a in root_cause or root_cause in PLAYBOOK_DIFFS[a][1][0].get("_why", ""):
            return a
    return "per_frame_heap_alloc_in_hot_path"   # CPU 四类共享模板，兜底取首个


def _pattern_id(issue_d: dict, top: dict) -> str:
    import hashlib
    sig = f"{issue_d['metric']}|{top['root_cause']}|{PB}"
    return "pat-" + hashlib.sha256(sig.encode()).hexdigest()[:10]


def _snap_from(st: dict):
    from upi.tooling.snapshot import Snapshot
    return Snapshot(**st["snapshot"])
