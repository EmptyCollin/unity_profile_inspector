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
from upi.config.project_profile import active_profile
from upi.config.settings import load_config
from upi.core.approval import ApprovalStore, build_request
from upi.core.detector import DEFAULT_REGISTRY
from upi.core import events as E
from upi.core.models import EditPlan, Hypothesis, Issue
from upi.tooling import snapshot as snapmod

# ───────────────── 工程上下文（阶段 7：由 ProjectProfile 注入）─────────────────
# ⚠️ 这里**不在模块级取快照**，而是每次调用现取 active_profile()：
#   写成 PROJECT = active_profile().abs_project 这类模块常量，值在导入瞬间
#   就冻结了，同一进程里 use_profile() 换工程后代码仍读旧路径——
#   "不改代码即可切换工程"（阶段 7 完成判据）会假通过：测试只要先 import
#   再切档案，拿到的还是上一个工程的路径，却看不出问题。
#   内置档案的派生值与旧字面量**逐字一致**，所以既有回归不变（验收线有断言钉）。
def _prof():
    return active_profile()


def _project() -> Path:
    return _prof().abs_project


def _merged_candidates(limit: int = 12):
    """采集端两遍 replay 的合并候选（含 light+deep）。"""
    try:
        from upi.core.candidates_v2 import CandidateV2
        files = sorted(E.RUNS.glob("*/merged_candidates.json"),
                       key=lambda p: p.stat().st_mtime)
        if not files:
            return []
        raw = json.loads(files[-1].read_text(encoding="utf-8"))
        return [CandidateV2(**c) for c in raw][:limit]
    except Exception:  # noqa: BLE001
        return []


def _scene_for(subject: str) -> Optional[Path]:
    """按机制取场景路径（回滚/重采要的是**被改的那个机制**的场景）。

    与旧实现的区别要说清：旧代码 SCENE 写死 Scene_F-CPU-01.unity，
    而 fix/verify 实际会处理 detection 出的任意夹具。按 subject 取才正确——
    只有 F-CPU-01 有修复模板，所以现状下二者取值相同（回归不变），
    将来多个机制有模板时才显出差别。
    """
    p = _prof()
    return p.scene_of(subject) if subject else None


def _ctx(st: dict) -> tuple[Path, Optional[Path]]:
    """从 state 取 (工程根, 场景)：state 显式值优先，其次 issue 携带，最后档案派生。"""
    iss = st.get("issue") or {}
    proj = st.get("project")
    if proj:
        proj = Path(proj)
    else:
        proj = _project()
    scene = st.get("scene") or iss.get("scene")
    if scene:
        return proj, Path(scene)
    try:
        subject = iss.get("fixture") or _prof().fix_subject
        return proj, _scene_for(subject)
    except Exception:  # noqa: BLE001 — 无档案/无场景：交给调用方按无场景处理
        return proj, None


def _why_not_auto(plan: EditPlan) -> str:
    """§3 维度一/二的落点：为什么这单要人来批（写进工单第 0 行，不让人猜）。"""
    if plan.permission_tier == "blocked":
        return ("夹具脚本 / 管线自身代码属 blocked 档（§3 维度一：永远人工），"
                "管线无自动执行权")
    if plan.side_effect == "may_affect_perception":
        return ("改动触碰感知层（材质/Shader/光照/动画/物理/相机），"
                "§3 维度一硬规则：无论置信度多高一律 semi")
    return ("不在语义保持白名单内（§3 维度二），auto 档必要条件不满足")

def make_nodes(run_dir: Path, *, live_llm: bool = False,
               approval_note: str = "",
               knowledge_db: Path | None = None,
               mode: str = "resident",
               evidence_collector=None) -> dict:
    """构造七节点函数表。live_llm=False 时 diagnose 用离线替身（不烧积分）。

    knowledge_db 可注入（单测写临时库，不污染仓库 patterns.db 与成功率分母）。"""

    def detect(st: dict) -> dict:
        # 产品默认：从运行证据包构造 Issue（不再跑夹具束的 m1_detect）。
        if True:  # I10: 只走运行证据
            try:
                from upi.core.candidates_v2 import generate_candidates
                from upi.core.evidence_diagnosis import load_latest_bundle
                from upi.core.models import Evidence
                bundle, ev_path = load_latest_bundle()
                root = st.get("project")
                if not root:
                    try:
                        root = _prof().abs_project
                    except Exception:  # noqa: BLE001
                        root = None
                cands = _merged_candidates(limit=1)
                if not cands:
                    cands = (generate_candidates(bundle, project_root=root, limit=1)
                             if bundle is not None else [])
            except Exception as e:  # noqa: BLE001 — 证据链不可用按“无证据”处理
                E.atomic_write_text(run_dir / "detect_evidence_error.txt",
                                    f"证据检测失败：{type(e).__name__}: {e}\n")
                cands, bundle, ev_path = [], None, None
            if cands:
                c = cands[0]
                alloc = c.kind == "allocation"
                metric = "gc_alloc_per_frame" if alloc else "frame_time_ms"
                det_id = ("DET-CPU-GC-FRAMEALLOC" if alloc
                          else "DET-LOAD-FRAMESPIKE")
                sig = {"dim": "gc" if alloc else "frame", "metric": metric,
                       "mechanism": c.mechanism, "threshold": 0.0,
                       "threshold_source": "evidence(no absolute line)"}
                ev = Evidence(tool="evidence_v2", raw_ref=str(ev_path),
                              value=float(c.score),
                              unit="bytes" if alloc else "ms", data_tier="A2")
                model = Issue(signature=sig, link="cpu", evidence=[ev],
                              magnitude=float(c.score), deviation_ratio=0.0,
                              explains_share_of_regression=1.0,
                              state="confirmed")
                issue = {"id": f"ISS-{bundle.run_id}",
                         "fixture": (st.get("fixture") or bundle.project_id
                                     or "project"),
                         "detector": det_id, "metric": metric,
                         "magnitude": float(c.score), "deviation_ratio": 0.0,
                         "evidence": {"source": "evidence_v2",
                                      "ref": str(ev_path), "score": c.score},
                         "state": "confirmed", "anchor": c.mechanism,
                         "signature": sig, "source": "evidence",
                         "scene": bundle.scene,
                         "issue_model": model.model_dump()}
                return {"findings": [{"kind": "issue",
                                      "fixture": issue["fixture"],
                                      "metric": metric,
                                      "magnitude": float(c.score),
                                      "detector": det_id,
                                      "signature": sig}],
                        "issue": issue, "route": "proceed",
                        "detection_ref": str(ev_path)}
            if bundle is not None:
                return {"route": "insufficient_evidence",
                        "exit_code": "insufficient_evidence",
                        "error": "证据包存在但没有工程内候选（证据不足，I2）"}
            return {"route": "insufficient_evidence",
                    "exit_code": "insufficient_evidence",
                    "error": ("insufficient_evidence：没有运行证据包"
                              "（先跑一轮证据采集；夹具束仅 legacy 开关可用）")}

    def diagnose(st: dict) -> dict:
        issue_d = st["issue"]
        det = next((d for d in DEFAULT_REGISTRY if d.id == issue_d.get("detector")),
                   None)
        if "issue_model" in issue_d:
            issue = Issue(**issue_d["issue_model"])
        elif det is not None:
            issue = _rebuild_issue(det, issue_d)
        else:
            return {"route": "insufficient_evidence",
                    "exit_code": "insufficient_evidence",
                    "error": "Issue 既无 issue_model 也无已知 detector（I2）"}
        # 产品默认：诊断来自运行证据候选（不读夹具目录、不匹配代码文本）。
        ev_cands = []
        ev_path = None
        if True:  # I10: 只走运行证据
            try:
                from upi.core.evidence_diagnosis import candidates_from_latest
                root = st.get("project")
                if not root:
                    root = _prof().abs_project
                ev_cands, _bundle, ev_path = candidates_from_latest(root, limit=12)
                merged = _merged_candidates(limit=12)
                if merged:
                    ev_cands = merged
            except Exception as e:  # noqa: BLE001 — 证据链不可用不阻断，按“无证据”处理
                E.atomic_write_text(run_dir / "diagnose_evidence_error.txt",
                                    f"证据候选读取失败：{type(e).__name__}: {e}\n")
        if not ev_cands:
            return {"route": "insufficient_evidence",
                    "exit_code": "insufficient_evidence",
                    "error": ("insufficient_evidence：没有运行证据候选"
                              "（不读夹具目录/代码正则；先跑一轮证据采集）"),
                    "tokens_used": 0}
        if not live_llm:
            # I10：诊断主体是 LLM；离线只做测量，不给“结论”
            return {"route": "not_measured",
                    "exit_code": "not_measured",
                    "error": "未测推理：诊断必须由 LLM 给出（live_llm=False）",
                    "tokens_used": 0,
                    "diagnose_source": "offline_not_measured"}
        # 真 LLM 诊断：证据候选 → 六因子判定（提示词已做泄漏筛查）
        from upi.agents.factor_judge import build_hypotheses_for_issue
        from upi.core.evidence_diagnosis import anchors_of
        from upi.core.mode_caps import caps as _caps_of
        mcaps = _caps_of(mode)
        cfg = load_config(ROOT / "llm_config.yaml")
        hist = ""
        try:
            from upi.core.knowledge import KnowledgeStore, default_path
            _ks = KnowledgeStore(knowledge_db or default_path())
            rows = _ks.list_patterns(5)
            _ks.close()
            hist = "; ".join(
                f"{r['pattern_id']}（tier={r['tier']}, playbook={r['playbook_id']}）"
                for r in rows)
        except Exception:  # noqa: BLE001 — 历史只是参考，读不到就不附
            hist = ""
        _pname = Path(str(root)).name if root else ""
        if _pname:
            hist = hist.replace(_pname, "project")
        tuples = [(c.root_cause, [a.replace(str(root), "").replace(
            _pname, "project") for a in anchors_of(c)])
            for c in ev_cands[:12]]
        cards: dict[str, str] = {}
        for c in ev_cands[:12]:
            spans = "; ".join(
                f"{s.file}:{s.start_line}-{s.end_line}"
                f"({s.symbol})" for s in (c.sources or [])[:3])
            span_text = (spans or "unknown").replace(str(root), "")
            if _pname:
                span_text = span_text.replace(_pname, "project")
            span_text = span_text.replace("\\", "/")
            cards[c.root_cause] = (
                f"mechanism={c.mechanism}; kind={c.kind}; "
                f"score={c.score:.2f} (CI {c.score_interval_low:.2f}-"
                f"{c.score_interval_high:.2f}); evidence_n={c.evidence_n}; "
                f"spans={span_text}")
        archive: dict = {}
        ev_rounds = 0
        while True:
            hs, judged = build_hypotheses_for_issue(
                cfg, issue, tuples, history=hist, cards=cards)
            archive.update(judged)
            from upi.core.attribution import route as _route
            if (_route(hs) != "needs_human"
                    or ev_rounds >= mcaps.max_evidence_rounds
                    or evidence_collector is None):
                break
            # I11c：分析定不了因 → 向检查发结构化补采请求；≤ caps 轮
            from upi.core.evidence_request import EvidenceRequest
            top = hs[0] if hs else None
            anchor = (top.code_anchors[0] if top and top.code_anchors else "")
            fpart, _, mpart = anchor.partition("::")
            req = EvidenceRequest(
                kind=("replay_window" if mcaps.allow_replay else "live_window"),
                reason="Top1 归因置信度不足，请补采同窗口证据",
                metric=(det.metric if det is not None else ""),
                window=32, file=fpart, method=mpart,
                expected="目标指标在窗口内的逐帧/分配证据")
            ev_rounds += 1
            try:
                delta = evidence_collector(req) or {}
            except Exception as e:  # noqa: BLE001
                delta = {"available": False,
                         "reason": f"{type(e).__name__}: {e}"}
            if not delta.get("available"):
                hist += ("\n补采不可用：" + str(delta.get("reason", ""))[:200])
                break
            text = str(delta.get("text", ""))[:2000]
            hist += f"\n补采证据（{req.kind}）：{text}"
            E.write_json(run_dir / f"evidence_round_{ev_rounds}.json",
                         {"request": req.model_dump(), "delta": delta},
                         what="evidence_round", strict=False)
        tokens = sum((a.get("_meta", {}).get("token_in", 0) or 0)
                     + (a.get("_meta", {}).get("token_out", 0) or 0)
                     for a in archive.values())
        E.write_json(run_dir / "diagnose_archive.json", archive,
                     what="diagnose_archive", strict=False)
        diag_source = "evidence_llm"
        # 预期效应回填（实测工单暴露：expected_effect 空 → 工单第 3 件套
        # 只能写"请拒批并要求补证"）。数值不是新估计——min_delta 直接搬运
        # 标定绝对阈（I1：来自标定工具，且 G1 判定本就以它为零改善线，
        # "修复至少要压下 k·σ/min_magnitude 才算数"是判据的重述不是承诺）。
        if det is not None:
            exp = {"metric": det.metric, "direction": "down",
                   "min_delta": det.thresholds.min_magnitude,
                   "source": f"calibration:{det.calibration_file or 'inline'}",
                   "note": "改善低于此线 G1 即判 not_significant（预期=判据重述）"}
            hs = [h.model_copy(update={"expected_effect": exp}) for h in hs]
        # 假设台账：Top1 记 adopted，其余记 not_adopted（不是失败，但要有结局字段）。
        # 台账写失败不打断诊断，但必须留痕，否则"落选假设没有记录"会静默复发。
        try:
            from upi.core.knowledge import KnowledgeStore, default_path
            _ks = KnowledgeStore(knowledge_db or default_path())
            _ks.record_hypotheses(
                run_dir.name, _issue_key(issue_d),
                [{"root_cause": h.root_cause, "confidence": h.confidence}
                 for h in hs])
            _ks.close()
        except Exception as e:  # noqa: BLE001
            E.atomic_write_text(
                run_dir / "hypotheses_error.txt",
                f"假设台账写入失败：{type(e).__name__}: {e}\n")
        from upi.core.attribution import route as attr_route
        decision = attr_route(hs)
        if decision == "needs_human":
            return {"hypotheses": [h.model_dump() for h in hs],
                    "route": "insufficient_evidence",
                    "exit_code": "insufficient_evidence",
                    "error": f"Top1 归因置信度低于门槛（attr_route={decision}）",
                    "tokens_used": tokens, "diagnose_source": diag_source}
        return {"hypotheses": [h.model_dump() for h in hs], "route": "proceed",
                "tokens_used": tokens, "diagnose_source": diag_source}

    def plan(st: dict) -> dict:
        # continue_with_direction 回环时跳过已 attempt 过的根因——重选假设，
        # 而不是对着同一条路再撞一次（§1.6 reason 回传给 Fix 的最小落实）
        tried = {r.get("root_cause") for r in st.get("records", [])
                 if r.get("kind") == "fix"}
        if not live_llm:
            return {"route": "not_measured", "exit_code": "not_measured",
                    "error": "规划必须由 LLM 给出（live_llm=False ⇒ 未测推理）"}
        issue_d = st.get("issue") or {}
        det = next((d for d in DEFAULT_REGISTRY if d.id == issue_d.get("detector")),
                   None)
        if "issue_model" in issue_d:
            issue = Issue(**issue_d["issue_model"])
        elif det is not None:
            issue = _rebuild_issue(det, issue_d)
        else:
            return {"route": "no_plan", "exit_code": "no_plan",
                    "error": "缺少 Issue 模型，无法规划"}
        try:
            root = _prof().abs_project
        except Exception:  # noqa: BLE001
            root = st.get("project")
        if not root:
            return {"route": "blocked", "exit_code": "blocked",
                    "error": "未选择工程，无法规划"}
        from upi.agents.llm_planner import plan_issue
        cfg = load_config(ROOT / "llm_config.yaml")
        hist = ""
        try:
            from upi.core.knowledge import KnowledgeStore, default_path
            _ks = KnowledgeStore(knowledge_db or default_path())
            rows = _ks.list_patterns(5)
            _ks.close()
            hist = "; ".join(
                f"{r['pattern_id']}（tier={r['tier']}, playbook={r['playbook_id']}）"
                for r in rows)
        except Exception:  # noqa: BLE001
            hist = ""
        blacklisted = 0
        for top in st["hypotheses"]:
            if top["root_cause"] in tried:
                continue
            # I25：生产侧黑名单只在这里查；考试路径不 import
            try:
                from upi.core.finding_blacklist import BlacklistStore
                from upi.core.repair_policy import should_skip
                _bl = BlacklistStore(ROOT / "runs" / "blacklist.db")
                _hit = should_skip(
                    _bl, issue.model_dump(),
                    window=str(st.get("window") or "default"),
                    metric=issue.signature.metric,
                    protocol_fingerprint=str(
                        st.get("protocol_fingerprint") or ""),
                    project_scope=str(getattr(_prof(), "profile_id", "") or ""))
            except Exception:  # noqa: BLE001
                _hit = None
            if _hit is not None:
                E.write_json(run_dir / "blacklist_skip.json",
                             {"fingerprint_id": _hit.fingerprint_id,
                              "signature": _hit.signature,
                              "reason": _hit.reason},
                             what="blacklist_skip", strict=False)
                tried.add(top["root_cause"])
                blacklisted += 1
                continue
            ep, meta = plan_issue(cfg, issue, top, root, history=hist)
            E.write_json(run_dir / "plan_llm.json",
                         {"root_cause": top["root_cause"], "meta": meta},
                         what="plan_llm", strict=False)
            if ep is not None:
                return {"edit_plan": ep.model_dump(), "route": "proceed",
                        "llm_plan": meta}
        if blacklisted:
            return {"route": "no_plan", "exit_code": "no_plan",
                    "error": f"黑名单命中 {blacklisted} 条，未规划"}
        return {"route": "no_plan", "exit_code": "no_plan",
                "error": f"LLM 未给出可执行方案（试过 {len(tried)} 条）"}

    def fix(st: dict) -> dict:
        ep = EditPlan(**st["edit_plan"])
        fixture = st["issue"]["fixture"]
        top = st["hypotheses"][0]
        # 闭包变量在函数内赋值会整体变成局部变量（条件里先读会
        # UnboundLocalError）——批准文本一律走局部 note，不回写外层。
        note = approval_note or ""
        # 协议指纹串带 profile 归属（阶段 7 批 2）：它落进 snapshot / state.json，
        # 是"这次 attempt 在哪个工程上做"的自描述。⚠️ 不作为 run 内对比闸——
        # 改前后同属一次进程、同一个档案，那种对比由 verify.assert_comparable
        # 查仪器状态；跨工程的真正硬闸在标定侧（m1_detect 的 G7 比较含
        # profile_id），标定只能来自它自己标出来的工程。
        stamp = time.strftime("%H%M%S")
        proj, scene = _ctx(st)
        try:
            _p = _prof()
            _pid, _groot = _p.profile_id, _p.git_root
        except Exception:  # noqa: BLE001 — 未选择工程时用工程自身作快照基准
            _pid, _groot = "unknown", Path(proj).resolve()
        fp = ("warmup=8,window=20,poll=1.0,resident=breachprobe+replaydriver+a2,"
              f"replay=none-v0,profile={_pid}")
        # root 一律取 profile.git_root：diff 的 file、快照脏区、apply 落盘
        # 三者共用同一个基准，才不会出现"改的是 A、申报的是 B"。
        snap = snapmod.snapshot(f"m4-{stamp}", proj, scene, fp,
                                root=_groot)

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
        evidence_mode = (st.get("issue", {}).get("source") == "evidence")
        if evidence_mode:
            before = None
            floor_before, fb_err = None, "evidence mode: 无夹具基线"
        else:
            before = resample(fixture, f"{run_dir.name}/pre_attempt_{stamp}")
            floor_before, fb_err = None, None
            try:
                floor_before = gc_floor_measure(fixture)
            except Exception as e:  # noqa: BLE001 — 缺侧即盲区，fail-open 到未评估
                fb_err = str(e)[:160]

        before_obs, before_err = None, None
        if evidence_mode and st.get("replay"):
            try:
                from upi.agents import replay_observe
                anchors = [a for h in (st.get("hypotheses") or [])[:1]
                           for a in h.get("code_anchors", [])]
                cands = replay_observe.capture(proj, st["replay"],
                                               f"{run_dir.name}_pre", 8)
                before_obs = replay_observe.observe(cands, anchors)
            except Exception as e:  # noqa: BLE001 — 观测失败不阻断修复，只留盲区
                before_err = f"{type(e).__name__}: {str(e)[:140]}"

        rep = fixer.execute(ep, snap, project_path=proj, scene_path=scene,
                            root=_groot, human_approved=bool(note.strip()),
                            approval_note=note)
        rec = {"kind": "fix", "ts": stamp, "git_ref": snap.git_ref,
               "root_cause": top["root_cause"],
               "snapshot": snap.to_dict(), "execute": rep}
        if rep["status"] != "applied":
            return {"snapshot": snap.to_dict(), "route": "needs_human",
                    "error": f"fix {rep['status']}: {rep.get('why','')[:120]}",
                    "records": st.get("records", []) + [rec]}
        pre = {"stamp": stamp,
               "before_path": (before or {}).get("_path", ""),
               "floor_before": floor_before, "floor_before_err": fb_err,
               "evidence": evidence_mode,
               "before_obs": before_obs, "before_obs_err": before_err}
        # attempts 不在这里 +1（真机实测撞出的语义错）：配额计的是
        # "评估过一次的完整尝试"。verify 崩溃（仪器漂移/编辑器消失）会
        # 回滚且不出 verdict——那是一次作废，不该吃掉 G11 的唯一配额，
        # 否则环境抖一下就把重试机会烧光（死锁实测：resume 被
        # budget_exceeded(attempts) 永远拦死）。计数移到 verify 出判定处。
        return {"snapshot": snap.to_dict(), "pre_attempt": pre,
                "records": st.get("records", []) + [rec], "route": "proceed"}

    def verify(st: dict) -> dict:
        pre = st["pre_attempt"]                  # fix 节点写入前锁定的基线
        if pre.get("evidence"):
            if st.get("replay"):
                # 单次 before/after 只能给 observed；verified 需 3 基线（G21）
                from upi.agents import replay_observe
                from upi.core.regression import RegressionVerdict
                proj = _ctx(st)[0]
                anchors = [a for h in (st.get("hypotheses") or [])[:1]
                           for a in h.get("code_anchors", [])]
                before = pre.get("before_obs")
                try:
                    cands = replay_observe.capture(proj, st["replay"],
                                                   f"{run_dir.name}_after", 8)
                    after = replay_observe.observe(cands, anchors)
                    observed, why = replay_observe.judge(before, after)
                except Exception as e:  # noqa: BLE001
                    observed = "unknown"
                    after = None
                    why = f"复验失败：{type(e).__name__}: {str(e)[:140]}"
                v = RegressionVerdict(
                    status="insufficient", verification_status="unverified",
                    target_cluster_id="", target_present=(after is not None),
                    new_clusters=[], guard_regressions=[], unassessed_guards=[],
                    reason=(f"observed={observed}：{why}"
                            "（单次前后，未达 3 基线；不记 verified）"),
                    protocol_fingerprint="")
                return {"verdict": v.model_dump(),
                        "attempts": st.get("attempts", 0) + 1,
                        "records": st.get("records", []) + [{
                            "kind": "verify", "status": "insufficient",
                            "verification_status": "unverified",
                            "observed": observed, "why": why}],
                        "route": "proceed"}
            # 没有 replay：已应用但无法复验，明确交人
            return {"route": "needs_human",
                    "records": st.get("records", []) + [{
                        "kind": "verify", "status": "unverified",
                        "why": "证据模式：无 replay 套件，未做同协议复验"}],
                    "error": ("证据模式：补丁已应用，但缺少 replay 套件，"
                              "无法同协议复验（unverified）")}
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
        # 规则先行（§4 硬门禁优先于一切 Agent 判断）；模型只在"未定"裁定点
        # **收紧**（#37 走 b：真接 llm_config 的 review 段，弱隔离——
        # DESIGN §2.3.5 明标，产物必须如实标注，不能让读者以为模型级隔离）。
        v = st["verdict"]
        status = v["status"] if isinstance(v, dict) else v.status
        n_review = len([r for r in st.get("records", []) if r.get("kind") == "review"]) + 1
        rec = {"kind": "review", "round": n_review, "verdict_status": status}
        budget = st["budget"]["max_review_rounds"]
        tokens = 0
        if status == "perception_failed":
            # G3：感知门禁失败 → 无条件回滚 + 转人工。**不烧配额**（§1.6 配额节），
            # 所以不看 n_review。当前 verify 恒 pending 产不出此状态（M7 T8 前
            # 感知门禁缺席是如实声明的），这条分支是给门禁落地预留的活路——
            # 没有它，T8 上线那天感知失败会被当成普通未解决去烧迭代轮。
            _p, _s = _ctx(st)
            rec["rollback"] = snapmod.rollback(_snap_from(st), _p, _s)
            decision = "escalate_human"
        elif status == "regressed":
            # 引入新问题 → 撤回重评（回滚交 git，review 不自己改文件）
            _p, _s = _ctx(st)
            rb = snapmod.rollback(_snap_from(st), _p, _s)
            rec["rollback"] = rb
            decision = "rollback_and_replan" if n_review < budget else "escalate_human"
        elif status == "not_significant":
            decision = "continue_with_direction" if n_review < budget else "escalate_human"
        else:
            decision = "escalate_human"

        # ── 模型收紧（只在规则给"继续/重评"这类未定点时介入；escalate 不送模型）──
        adj_meta: dict = {"adjudication": "rule_only"}
        if live_llm and decision != "escalate_human":
            from upi.agents import review_adjudicate as RA
            _ep = st.get("edit_plan") or {}
            _hyp = (st.get("hypotheses") or [{}])[0]
            _vd = v if isinstance(v, dict) else v.model_dump()
            inputs = RA._whitelist_input(
                status=status, target=_vd.get("target", {}),
                guards=[g if isinstance(g, dict) else g.model_dump()
                        for g in _vd.get("guards", [])],
                conflicts=[c if isinstance(c, dict) else c.model_dump()
                           for c in _vd.get("conflicts", [])],
                perception=_vd.get("perception", {}) if isinstance(
                    _vd.get("perception"), dict) else
                    (_vd.get("perception").model_dump()
                     if _vd.get("perception") else {}),
                compile_ok=_vd.get("compile_ok", False),
                realized=_vd.get("realized_over_expected", 0.0),
                root_cause=str(_hyp.get("root_cause", "")),
                side_effect=str(_ep.get("side_effect", "unknown")),
                diff_files=[str(d.get("file", "")) for d in (_ep.get("diffs") or [])])
            try:
                decision, adj_meta = RA.adjudicate(
                    load_config(ROOT / "llm_config.yaml"),
                    rule_decision=decision, inputs=inputs)
                adj_meta["adjudication"] = "model_tightened_or_kept"
                tokens = (adj_meta.get("token_in") or 0) + (adj_meta.get("token_out") or 0)
            except Exception as e:  # noqa: BLE001 — 裁定失败降级回规则，绝不卡循环
                adj_meta = {"adjudication": "model_failed_degraded_to_rule",
                            "error": f"{type(e).__name__}: {str(e)[:160]}"}
        rec["adjudication"] = adj_meta
        E.write_json(run_dir / "review_archive.json",
                     {"round": n_review, "rule_inputs_status": status,
                      "final_decision": decision, "meta": adj_meta},
                     what="review_archive", strict=False)
        # §1.6 输出：结构化 reason 回传 Fix（旧版只有 "verdict=… round=…" 一行文本，
        # Fix 端只能靠根因名去重）。JSON 字符串承载：模型判据、据以判断的字段、
        # 弱隔离声明——plan 侧可解析（本批 plan 未消费，字段先立住再有牙齿）。
        reason = json.dumps(
            {"verdict": status, "round": n_review,
             "rule_or_model": adj_meta.get("adjudication"),
             "model_reason": adj_meta.get("reason", ""),
             "evidence_fields": adj_meta.get("evidence_fields", []),
             "isolation": adj_meta.get("isolation",
                                       "rule-only（未启用模型或未到期）")},
            ensure_ascii=False)
        return {"review_decision": decision,
                "review_reason": reason,
                "records": st.get("records", []) + [rec], "route": "proceed",
                "tokens_used": tokens}

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
            # 协议指纹：账本侧也要能回答"这两次是不是同一把尺子"（G7）。
            try:
                from upi.core.protocol import build as _fp_build, digest as _fp_digest
                sp = st.get("sampling_protocol") or {}
                pfp = _fp_digest(_fp_build(
                    scene=st.get("scene", ""),
                    quality_tier=st.get("quality_tier", ""),
                    data_tier=sp.get("data_tier", ""),
                    editor_mode=(st.get("editor") or {}).get("mode", ""),
                    replay_script_hash=st.get("replay_script_hash", "none-v0"),
                    scale_factor=sp.get("scale_factor", 1),
                    poll_rate=sp.get("poll_rate", 0),
                ))
            except Exception:  # noqa: BLE001
                pfp = ""
            ks.record_outcome(pid, run_dir.name, st["issue"]["fixture"],
                              st["quality_tier"], st_status,
                              metric_delta=(v.get("target", {}).get("delta")
                                            if isinstance(v, dict) else None),
                              protocol_fp=pfp)
            ks.record_hypothesis_outcome(_issue_key(st["issue"]), st_status)
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
                E.atomic_write_text(
                    run_dir / "checklist_error.txt",
                    f"体检表计算失败，提案按缺项处理：{type(e).__name__}: {e}\n")
            prop = ks.propose_promotion(pid, "auto", overfit_checklist=cl)
            # 提案是本节点的结论，落盘失败必须留痕（strict=False 保证记账
            # 不因写文件失败而中断，但 write_failed 事件会进 UI 告警区）
            E.write_json(run_dir / "promotion_proposal.json",
                         prop.model_dump(), what="promotion_proposal",
                         strict=False)
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
        # I13d：任何终态都必须留下 exit.json（状态、原因、上下文）
        try:
            from upi.core.exits import record_exit
            _route = str(st.get("route") or "")
            _code = str(st.get("exit_code") or
                        {"needs_human": "blocked", "proceed": "failed"}.get(
                            _route, _route or "failed"))
            record_exit(run_dir, stage="escalate", code=_code,
                        reason=str(st.get("error") or _route or "terminal"),
                        mode=mode, details={"route": _route,
                                            "attempts": final["attempts"]})
        except Exception as e:  # noqa: BLE001 — 出口落盘失败不阻断报告
            E.atomic_write_text(run_dir / "exit_error.txt",
                                f"出口落盘失败：{type(e).__name__}: {e}\n")

        # 未验证写入不得留在工程里（路由测试实测撞出的缺口）：预算拦停发生在
        # fix 写入**之后**、verify 之前——夹具会停在修复态，既污染后续检出
        # （检测原料没了），也让"失败样本"带着未验证改动入库。判据见纯函数。
        recs = st.get("records", [])
        if _needs_final_rollback(recs, final.get("verdict_status")):
            try:
                _p, _s = _ctx(st)
                rb = snapmod.rollback(_snap_from(st), _p, _s)
                final["final_rollback"] = rb
            except Exception as e:  # noqa: BLE001 — 回滚失败必须可见，不静默
                final["final_rollback"] = {"error": str(e)[:200]}

        # 终态报告原子写 + 失败留痕：runreport.json 是统计层算成本与
        # 判定分布的唯一入口，半写会让该轮在报告里"凭空消失"（G13 的
        # untraced 判定依赖产物存在性）。失败不能打断已完成的记账，
        # 但必须把这件事说出去。
        if not E.write_json(run_dir / "runreport.json", final,
                            what="runreport", strict=False):
            E.emit("runreport_write_failed", run_id=run_dir.name)

        # 失败出口的人读总结（#24）：runreport.json 是给统计层的机器终态，
        # 这份是给**下一步该干什么**的人看的。
        # ⚠️ 触发条件必须排除成功路径——escalate 是统一出口，record（成功记账）
        #    也有一条边指进来。判据用"最后一次判定是否 resolved"而不是
        #    "有没有 error"：预算拦停会把 error 写进 state，但审批停在半路时
        #    verdict 可能已经是 resolved（只是没走到 record），那种情况
        #    出"修复未成"报告就是撒谎。
        if final.get("verdict_status") != "resolved":
            _det = None
            # 读 detect 记下的真实指针（runs/m1detect_*/detection.json），
            # 不是 run_dir 下的同名文件——那个位置根本不存在，写死路径的
            # 第一版会让画像永远静默缺失（假接线的又一次现形）。
            _dr = st.get("detection_ref")
            try:
                if _dr and Path(_dr).exists():
                    _det = json.loads(Path(_dr).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _det = None       # 画像缺失由报告自己声明，不在这里编数据
            try:
                from upi.tooling.failure_report import failure_summary_md
                # 摘要需要 run_id，而 final 里有；state 本身不带（run_dir 是闭包）
                E.atomic_write_text(
                    run_dir / "failure_summary.md",
                    failure_summary_md({**st, "run_id": run_dir.name}, _det))
            except Exception as e:  # noqa: BLE001 — 报告失败不许打断记账与回滚
                E.emit("failure_summary_failed", run_id=run_dir.name,
                       error=str(e)[:160])
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


def _pattern_id(issue_d: dict, top: dict) -> str:
    import hashlib
    sig = f"{issue_d['metric']}|{top['root_cause']}|{PB}"
    return "pat-" + hashlib.sha256(sig.encode()).hexdigest()[:10]


def _issue_key(issue_d: dict) -> str:
    """假设台账的 issue 主键：diagnose 与 record 两处必须算出同一个值。"""
    if issue_d.get("issue_id"):
        return str(issue_d["issue_id"])
    rel = issue_d.get("rel_frame", issue_d.get("rel", ""))
    return f"{issue_d.get('detector', '')}:{rel}"


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
