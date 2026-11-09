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
from upi.core.approval import ApprovalStore, build_request
from upi.core.candidate_retrieval import retrieve_candidates
from upi.core.detector import DEFAULT_REGISTRY
from upi.core.models import EditPlan, Hypothesis, Issue
from upi.tooling import snapshot as snapmod

PROJECT = ROOT / "fixtures" / "upi-fixture"
SCENE = PROJECT / "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity"
CSFILE = "fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs"


def _why_not_auto(plan: EditPlan) -> str:
    """§3 维度一/二的落点：为什么这单要人来批（写进工单第 0 行，不让人猜）。"""
    if plan.permission_tier == "blocked":
        return ("夹具脚本 / 管线自身代码属 blocked 档（§3 维度一：永远人工），"
                "管线无自动执行权")
    if plan.side_effect == "may_affect_perception":
        return ("改动触碰感知层（材质/Shader/光照/动画/物理/相机），"
                "§3 维度一硬规则：无论置信度多高一律 semi")
    return ("不在语义保持白名单内（§3 维度二），auto 档必要条件不满足")

# anchor 机制 → 该机制对应的语义保持修复（PB-* 取自 DET-CPU-GC-FRAMEALLOC.playbooks，
# 均 whitelisted / side_effect=none）。diff 模板与彩排同源，逐条对应证据。
# ⚠️ find 段取自**钉定的缺陷态参照**，不是工作区夹具：第三轮真跑走保留通路后
# 工作区已是修复态，若从现场切片，find 会与文件现存内容逐字相同——apply 报
# "成功"但实为原地空写（静默假成功，比报错更危险）。参照由 git 历史版本固化，
# 内容校验不过就直接拒绝导入（宁可装不上，不可假修复）。
DEFECT_REF = Path(__file__).resolve().parent / "_refs" / "F_CPU_01_defect.cs"
_T = DEFECT_REF.read_text(encoding="utf-8").split("\n")
_lines = lambda a, b: "\n".join(_T[a - 1:b])
assert "var ready = new List<SkillRuntime>();" in "\n".join(_T), \
    "缺陷态参照缺少缺陷特征：diff 模板会退化成空写，已拒绝装载"


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


def _run_detect():
    """跑 m1_detect 并定位本轮 detection.json（模块级=测试可替换的边界）。

    stdout 末行 "trace: <abs>/trace.jsonl" 是唯一稳定指针——m1_detect 不落
    "最新"软链，也不该为它加副作用。返回 None = 没跑成/没产出，detect 节点
    按 needs_human 处理。"""
    import subprocess, sys
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "m1_detect.py")],
                       capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace")
    for ln in p.stdout.splitlines():
        if ln.startswith("trace:"):
            return Path(ln.split(":", 1)[1].strip()).parent / "detection.json"
    return None


def make_nodes(run_dir: Path, *, live_llm: bool = False,
               approval_note: str = "",
               knowledge_db: Path | None = None) -> dict:
    """构造七节点函数表。live_llm=False 时 diagnose 用离线替身（不烧积分）。

    knowledge_db 可注入（单测写临时库，不污染仓库 patterns.db 与成功率分母）。"""

    def detect(st: dict) -> dict:
        # 复用 m1_detect 的判据链，但只取"当前夹具"这一条 Issue 进循环
        det_fp = _run_detect()
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
                    "error": "detect 未在当前夹具检出 Issue"
                             f"（detection.json={'缺失' if det_fp is None else det_fp.name}）"}
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
        # 预期效应回填（实测工单暴露：expected_effect 空 → 工单第 3 件套
        # 只能写"请拒批并要求补证"）。数值不是新估计——min_delta 直接搬运
        # 标定绝对阈（I1：来自标定工具，且 G1 判定本就以它为零改善线，
        # "修复至少要压下 k·σ/min_magnitude 才算数"是判据的重述不是承诺）。
        exp = {"metric": det.metric, "direction": "down",
               "min_delta": det.thresholds.min_magnitude,
               "source": f"calibration:{det.calibration_file or 'inline'}",
               "note": "改善低于此线 G1 即判 not_significant（预期=判据重述）"}
        hs = [h.model_copy(update={"expected_effect": exp}) for h in hs]
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
        fixture = st["issue"]["fixture"]
        top = st["hypotheses"][0]
        # 闭包变量在函数内赋值会整体变成局部变量（条件里先读会
        # UnboundLocalError）——批准文本一律走局部 note，不回写外层。
        note = approval_note or ""
        fp = "warmup=8,window=20,poll=1.0,resident=breachprobe+replaydriver+a2,replay=none-v0"
        stamp = time.strftime("%H%M%S")
        snap = snapmod.snapshot(f"m4-{stamp}", PROJECT, SCENE, fp, root=ROOT)

        # ── 先报后批（M4 / I3：修改权限只来自权限矩阵，不来自模型自评）──
        # 顺序讲究：快照先建（秒级、无副作用，且工单第 4 件套要写真实 tag），
        # 工单递交并等人决定；**未获批准就不采基线**——单夹具重采一轮约 2.4 分钟，
        # 被拒时那笔成本是白烧的。批准文本只在这里读入，fixer 的闸照旧独立生效。
        if ep.permission_tier != "auto" and not note.strip():
            store = ApprovalStore(run_dir)
            issue_obj = _rebuild_issue(
                next(d for d in DEFAULT_REGISTRY
                     if d.id == st["issue"]["detector"]), st["issue"])
            hyp = Hypothesis(**top)
            req = build_request(run_id=run_dir.name, issue=issue_obj,
                                hypothesis=hyp, plan=ep,
                                permission_tier=ep.permission_tier,
                                reason_not_auto=_why_not_auto(ep),
                                git_ref=snap.git_ref)
            where = store.submit(req)
            dec = store.load_decision(req.request_id)
            if dec is None:
                rec = {"kind": "fix", "ts": stamp, "git_ref": snap.git_ref,
                       "root_cause": top["root_cause"],
                       "snapshot": snap.to_dict(),
                       "execute": {"status": "awaiting_approval",
                                   "request_id": req.request_id}}
                return {"snapshot": snap.to_dict(), "route": "needs_human",
                        "error": f"等待人工批准：{where['md']}",
                        "approval_request": where,
                        "records": st.get("records", []) + [rec]}
            if dec.decision != "approve":
                rec = {"kind": "fix", "ts": stamp, "git_ref": snap.git_ref,
                       "root_cause": top["root_cause"],
                       "snapshot": snap.to_dict(),
                       "execute": {"status": "rejected", "decision":
                                   dec.model_dump()}}
                return {"snapshot": snap.to_dict(), "route": "needs_human",
                        "error": f"人工{'拒绝' if dec.decision=='reject' else '搁置'}"
                                 f"（{dec.approver}：{dec.note[:80]}）",
                        "records": st.get("records", []) + [rec]}
            note = dec.as_approval_note()
            store.record_used(req.request_id, dec, {"stage": "granted"})

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
                            root=ROOT, human_approved=bool(note.strip()),
                            approval_note=note)
        rec = {"kind": "fix", "ts": stamp, "git_ref": snap.git_ref,
               "root_cause": top["root_cause"],
               "snapshot": snap.to_dict(), "execute": rep}
        if rep["status"] != "applied":
            return {"snapshot": snap.to_dict(), "route": "needs_human",
                    "error": f"fix {rep['status']}: {rep.get('why','')[:120]}",
                    "records": st.get("records", []) + [rec]}
        pre = {"stamp": stamp, "before_path": before["_path"],
               "floor_before": floor_before, "floor_before_err": fb_err}
        # attempts 不在这里 +1（真机实测撞出的语义错）：配额计的是
        # "评估过一次的完整尝试"。verify 崩溃（仪器漂移/编辑器消失）会
        # 回滚且不出 verdict——那是一次作废，不该吃掉 G11 的唯一配额，
        # 否则环境抖一下就把重试机会烧光（死锁实测：resume 被
        # budget_exceeded(attempts) 永远拦死）。计数移到 verify 出判定处。
        return {"snapshot": snap.to_dict(), "pre_attempt": pre,
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
        # 判定产出才计 attempt（与 fix 侧注释配对）：走到这里说明这次
        # 写入已被同一协议双侧采样评估过，无论 resolved/regressed 都算
        # 消耗了一次完整尝试；反之 fix 落地后 verify 崩溃不计数。
        return {"verdict": v.model_dump(),
                "attempts": st.get("attempts", 0) + 1,
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
        ks = KnowledgeStore(knowledge_db or (ROOT / "knowledge" / "patterns.db"))
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
            # §1.7：record 的完整职责是"写入 + 提出升维建议"。建议**只落盘不执行**
            # （权限变更须人工批准，I3）。
            # M6' 接入后体检表不再是"算不出就传 None"：能算的项附实际数值，
            # 让 blocked 理由精确到"缺哪几项 / 哪项不达标值是多少"，
            # 而不是一句笼统的"缺 §8.7 体检表"。算不出的项仍由 propose_promotion
            # 报缺（G14 的"缺项即 blocked"照旧生效，第一轮必然 blocked）。
            # 统计层出错绝不能打断记账本身，故兜底回退 None。
            try:
                from upi.tooling.checklist import checklist_for_proposal
                cl = checklist_for_proposal()
            except Exception as e:  # noqa: BLE001
                cl = None
                (run_dir / "checklist_error.txt").write_text(
                    f"体检表计算失败，提案按缺项处理：{type(e).__name__}: {e}\n",
                    encoding="utf-8")
            prop = ks.propose_promotion(pid, "auto", overfit_checklist=cl)
            (run_dir / "promotion_proposal.json").write_text(
                json.dumps(prop.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8")
            prop_note = {"kind": "record", "pattern_id": pid,
                         "proposal_status": prop.status,
                         "blocked_reasons": prop.blocked_reasons}
        finally:
            ks.close()
        return {"records": st.get("records", []) + [prop_note]}

    def escalate(st: dict) -> dict:
        final = {"run_id": run_dir.name, "fixture": st.get("fixture"),
                 "route": st.get("route"), "error": st.get("error"),
                 "attempts": st.get("attempts", 0), "spent": st.get("spent")}
        v = st.get("verdict")
        if v:
            final["verdict_status"] = v["status"] if isinstance(v, dict) else v.status

        # 未验证写入不得留在工程里（路由测试实测撞出的缺口）：预算拦停发生在
        # fix 写入**之后**、verify 之前——夹具会停在修复态，既污染后续检出
        # （检测原料没了），也让"失败样本"带着未验证改动入库。判据见纯函数。
        recs = st.get("records", [])
        if _needs_final_rollback(recs, final.get("verdict_status")):
            try:
                rb = snapmod.rollback(_snap_from(st), PROJECT, SCENE)
                final["final_rollback"] = rb
            except Exception as e:  # noqa: BLE001 — 回滚失败必须可见，不静默
                final["final_rollback"] = {"error": str(e)[:200]}

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


def _needs_final_rollback(records: list, verdict_status) -> bool:
    """出口收尾判据（纯函数，可离线单测——escalate 直接调 rollback 不可测）。

    True 的条件：最后一次 kind=fix 的 execute 已 applied，其后没有任何
    rollback（review 撤回时已回滚），且整轮没拿到 resolved。
    - resolved ⇒ False：修复被验证有效，改动要保留（不回滚）。
    - 最后 fix 未 applied（diff 被拒/编译失败已回滚）⇒ False：本就没脏写。
    - review 已 rollback_and_replan 回滚过 ⇒ False：不重复回滚。
    """
    if verdict_status == "resolved":
        return False
    last_fix = next((i for i in range(len(records) - 1, -1, -1)
                     if records[i].get("kind") == "fix"), None)
    if last_fix is None:
        return False
    if records[last_fix].get("execute", {}).get("status") != "applied":
        return False
    if any(r.get("rollback") for r in records[last_fix + 1:]):
        return False
    return True
