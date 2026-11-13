"""真实考试驱动：live LLM 诊断/规划 → 工单 → 执行 → 记账 → 评分。

这是“监考”脚本，不是产品路径；它只调用产品节点与工单模型，
不向 LLM 注入任何测试/答案信息（提示词筛查在 factor_judge/llm_planner 内）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.agents import nodes as N  # noqa: E402
from upi.agents.llm_planner import plan_issue  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.findings import (Anchor, AttributionInfo, RepairRecord,  # noqa: E402
                               RunFindings, ToolFinding, new_run_id, save_run,
                               score_run)
from upi.core.protocol import build as fp_build, digest as fp_digest, file_hash  # noqa: E402
from upi.core.mode_caps import caps  # noqa: E402
from upi.core.retry_policy import (is_stagnant, max_attempts,  # noqa: E402
                                   next_untried)
from upi.core.ticket import (BatchTicket, TicketItem, TicketStore,  # noqa: E402
                             _item_token, _token, approve, approve_item)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402
from upi.core.verify_ladder import evaluate  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402


def unity(*args, timeout=60):
    import subprocess as sp
    p = sp.run(["unity", "command", *args, "--project-path", "test_project",
                "--format", "json"], capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": p.stdout[-200:]}


def anchors_of_hyp(h: dict) -> list[str]:
    return [a for a in (h.get("code_anchors") or []) if a]


def anchor_objs(h: dict) -> list[Anchor]:
    out = []
    for a in anchors_of_hyp(h):
        if "::" in a:
            f, m = a.split("::", 1)
            out.append(Anchor(file=f, method=m.split(".")[-1]))
        else:
            f, _, line = a.rpartition(":")
            try:
                ln = int(line)
            except ValueError:
                f, ln = a, 0
            out.append(Anchor(file=f, start_line=ln, end_line=ln))
    return out


def findings_from_hypotheses(run_id: str, hs: list[dict], archive: dict) -> list[ToolFinding]:
    out = []
    for i, h in enumerate(hs):
        anchors = anchor_objs(h)
        if not anchors:
            continue
        raw = archive.get(h.get("root_cause"), {}) or {}
        refs = []
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            if isinstance(v, dict):
                refs.extend([str(x) for x in (v.get("refs")
                                              or v.get("evidence_refs") or [])])
        out.append(ToolFinding(
            finding_id=f"llm-{run_id}-{i}",
            mechanism=str(h.get("root_cause", "")).split(":")[0],
            metric="llm", link="",
            anchors=anchors, evidence_refs=sorted(set(refs)),
            attribution=AttributionInfo(confidence=float(h.get("confidence", 0)),
                                        factors={"root_cause": h.get("root_cause")}),
            state="candidate"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    a = ap.parse_args()
    p = Path(a.project).resolve()
    run_id = new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    chain: list[dict] = []
    t_start = time.time()

    def mark(step: str, status: str = "ok", detail: str = "") -> None:
        chain.append({"step": step, "status": status,
                      "at_s": round(time.time() - t_start, 2),
                      "detail": detail[:200]})

    def save_chain() -> None:
        (run_dir / "chain.json").write_text(json.dumps(
            {"steps": chain, "backtracks": max(0, len(run.attempts) - 1),
             "baseline": {"ran": False,
                          "reason": "本场只做单次 replay 复验；未跑 3 次基线，"
                                    "所以 verdict=observed 而非 verified"}},
            ensure_ascii=False, indent=2), encoding="utf-8")

    # 1) 证据采集（r 轻采 → 选窗 → r′ 窗口深采）
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "capability_run.py"),
                        "--project", str(p), "--project-id", a.project_id,
                        "--replay", a.replay, "--no-save", "--two-pass"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=2400)
    print("capture:", (r.stdout or r.stderr)[-300:])
    mark("capture(r+r')", "ok" if r.returncode == 0 else "failed",
         (r.stdout or r.stderr)[-160:])
    if r.returncode != 0:
        return 1
    from upi.core.evidence_diagnosis import candidates_from_latest
    _pre_cands, _, _ = candidates_from_latest(p, limit=12)
    before_roots0 = {str(c.root_cause).split(":", 1)[-1] for c in _pre_cands}

    # 2) detect → diagnose（live LLM）
    from upi.core.project_store import knowledge_db as _knowledge_db
    collector = _make_collector(p, a.project_id, a.replay)
    nd = N.make_nodes(run_dir, live_llm=True,
                      knowledge_db=_knowledge_db(a.project_id),
                      mode="regression",
                      evidence_collector=collector)
    d = nd["detect"]({"project": str(p)})
    mark("detect", d.get("route", ""), str(d.get("error", ""))[:120])
    print("detect:", d.get("route"), str(d.get("error", ""))[:120])
    if d.get("route") != "proceed":
        save_chain()
        return 1
    g = nd["diagnose"]({"issue": d["issue"], "project": str(p)})
    mark("diagnose", g.get("route", ""),
         f"tokens={g.get('tokens_used')} hyps={len(g.get('hypotheses') or [])}")
    print("diagnose:", g.get("route"), g.get("diagnose_source"),
          "tokens", g.get("tokens_used"))
    if g.get("route") != "proceed":
        print("diagnose error:", g.get("error"))
        save_chain()
        return 1
    hs = g.get("hypotheses") or []
    print("top1:", hs[0].get("root_cause"), "conf", hs[0].get("confidence"))
    archive = {}
    apath = run_dir / "diagnose_archive.json"
    if apath.exists():
        try:
            archive = json.loads(apath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            archive = {}

    # 3) 写 findings（LLM 诊断产物）
    findings = findings_from_hypotheses(run_id, hs, archive)
    run = RunFindings(
        run_id=run_id, project_id=a.project_id, mode="regression",
        protocol_fingerprint=fp_digest(fp_build(
            replay_script_hash=file_hash(Path(a.replay)), data_tier="A2",
            editor_mode="gui")),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        findings=findings, repairs=[])
    save_run(run)
    print("findings:", len(findings))

    # 4) LLM 规划（第一个能出方案的假设）
    plan = None
    meta = {}
    plan_hyp = None
    for h in hs:
        plan, meta = plan_issue(N.load_config(ROOT / "llm_config.yaml"),
                                _issue(d["issue"]), h, p)
        print("plan:", h.get("root_cause"), "->", bool(plan), meta.get("error", ""))
        if plan is not None:
            plan_hyp = h
            break
    mark("plan", "ok" if plan is not None else "failed",
         f"{(plan_hyp or {}).get('root_cause','')} playbook={getattr(plan,'playbook_id','')}")
    if plan is None:
        print("no LLM plan")
        save_chain()
        return 1

    # 5) 工单（人=用户代理，全部批准）
    store = TicketStore()
    tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-llm"
    iid = "it-llm"
    item = TicketItem(item_id=iid, cluster_id="llm", metric="llm",
                      shape="llm_plan", kind="llm", count=1,
                      pattern_id="llm", playbook_id=plan.playbook_id,
                      permission_tier=plan.permission_tier,
                      plan=plan.model_dump(), status="pending",
                      item_approve_token=_item_token(tid, iid))
    t = BatchTicket(ticket_id=tid, project_id="paper", mode="regression",
                    created_at=E.now(), status="awaiting_approval",
                    items=[item], approve_token=_token(tid, [item]))
    store.save(t)
    print("approve batch:", approve(t, approver="user",
                                    note="用户授权：全部批准",
                                    token=t.approve_token))
    if item.permission_tier != "auto":
        print("approve item:", approve_item(
            t, iid, approver="user", note="用户授权：全部批准",
            token=item.item_approve_token))
    store.save(t)

    def snap_fn(attempt, project, scene, fp):
        return SN.snapshot(attempt, project, scene, fp,
                           root=Path(project).resolve())

    def _replan(item, oldplan, err):
        """编译失败回喂：换一个假设重新出补丁（考试模式最多 2 次）。"""
        cfg = N.load_config(ROOT / "llm_config.yaml")
        issue_obj = _issue(d["issue"])
        for h in hs:
            np, _m = plan_issue(cfg, issue_obj, h, p)
            if np is not None:
                return np
        return None

    ex = UnityTicketExecutor(store, snapshot_fn=snap_fn, project=p,
                             scene=p / "Assets/Scenes/00_Bootstrap.unity",
                             mode="regression", replan=_replan)
    out = ex.execute(t)
    mark("execute#1", "ok" if out.items[0].status in
         ("applied_unverified",) else "failed",
         f"{out.items[0].status} {out.items[0].error[:80]}")
    print("execute:", out.status)
    for it in out.items:
        print(" item:", it.status, it.error[:100])

    # 6) 修复后 replay 一次：目标还在？出现新问题？（先不烧 3 次基线）
    ladder_status, target_gone, new_roots = "unverified", None, []
    try:
        from upi.core.verify_ladder import evaluate as _ladder
        before_roots = before_roots0
        # 修复后再采一次（同 replay、同协议）
        r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "capability_run.py"),
                             "--project", str(p), "--project-id", a.project_id,
                             "--replay", a.replay, "--no-save"],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=1800)
        print("post-capture:", (r2.stdout or r2.stderr)[-160:])
        after_cands, _, _ = candidates_from_latest(p, limit=12)
        after_roots = {str(c.root_cause).split(":", 1)[-1] for c in after_cands}
        target_rc = str((plan_hyp or {}).get("root_cause") or "").split(":", 1)[-1]
        target_gone = target_rc not in after_roots
        new_roots = sorted(after_roots - before_roots - {target_rc})
        ladder = _ladder("regression", replay_once=lambda: {
            "protocol_ok": True, "target_gone": target_gone,
            "new_clusters": new_roots, "guards_ok": True})
        ladder_status = ladder.status
        print("ladder:", ladder.status, ladder.reason)
        mark("verify#1", ladder.status, ladder.reason[:160])
        (run_dir / "verify_ladder.json").write_text(
            ladder.model_dump_json(indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("ladder error:", type(e).__name__, e)

    # 7) 受控 Agent loop：replay 一次不干净 → 换方案再试一次（regression 预算内）
    target = findings[0] if findings else None
    if target is not None:
        run.repairs.append(RepairRecord(
            finding_id=target.finding_id, plan_id=plan.playbook_id,
            files=plan.touched_assets,
            compile_ok=(out.items[0].status in ("applied", "applied_unverified")),
            runnable=None, verdict=ladder_status,
            attempt_no=1, ticket_id=t.ticket_id,
            diff_hash=_diff_hash(plan), stop_reason=ladder_status))
        run.attempts.append({
            "attempt_no": 1, "hypothesis": str((plan_hyp or {}).get("root_cause") or ""),
            "plan_id": plan.playbook_id, "diff_hash": _diff_hash(plan),
            "ticket_id": t.ticket_id, "item_status": out.items[0].status,
            "ladder": ladder_status, "stop_reason": ladder_status})
        save_run(run)
    retry_obs: dict = {}
    retry_stop = ""
    tried = {str((plan_hyp or {}).get("root_cause") or "")}
    seen_hashes = {_diff_hash(plan)}
    retry_round = 0
    max_steps = max_attempts("regression")
    if ladder_status == "needs_recheck" and target is not None:
        from upi.core.agent_loop import run_loop

        def _decide(state):
            last = state.get("last") or {}
            if retry_round > 0 and last.get("ladder") not in ("needs_recheck", None):
                return {"stop": True, "reason": "clean"}
            if retry_round >= max_steps:
                return {"stop": True, "reason": "budget"}
            return {"tool": "plan_patch"}

        def _act(_decision):
            nonlocal ladder_status, target_gone, new_roots, retry_round
            retry_round += 1
            cfg = N.load_config(ROOT / "llm_config.yaml")
            np, hsel = None, None
            for h in hs:
                if str(h.get("root_cause") or "") in tried:
                    continue
                cand, _m = plan_issue(cfg, _issue(d["issue"]), h, p)
                if cand is None:
                    tried.add(str(h.get("root_cause") or ""))
                    continue
                hh = _diff_hash(cand)
                if is_stagnant(hh, seen_hashes):
                    tried.add(str(h.get("root_cause") or ""))
                    continue
                np, hsel = cand, h
                break
            if np is None:
                return {"error": "no_plan_for_untried"}
            tried.add(str(hsel.get("root_cause") or ""))
            hh = _diff_hash(np)
            seen_hashes.add(hh)
            tid2 = ("bt-" + time.strftime("%Y%m%d_%H%M%S")
                    + f"-r{retry_round}")
            iid2 = f"it-r{retry_round}"
            it2 = TicketItem(item_id=iid2, cluster_id="llm-retry", metric="llm",
                             shape="llm_plan", kind="llm", count=1,
                             pattern_id="llm", playbook_id=np.playbook_id,
                             permission_tier=np.permission_tier,
                             plan=np.model_dump(), status="pending",
                             item_approve_token=_item_token(tid2, iid2))
            t2 = BatchTicket(ticket_id=tid2, project_id="paper",
                             mode="regression", created_at=E.now(),
                             status="awaiting_approval", items=[it2],
                             approve_token=_token(tid2, [it2]))
            store.save(t2)
            approve(t2, approver="user", note="用户授权：全部批准",
                    token=t2.approve_token)
            if it2.permission_tier != "auto":
                approve_item(t2, iid2, approver="user",
                             note="用户授权：全部批准",
                             token=it2.item_approve_token)
            store.save(t2)
            o2 = UnityTicketExecutor(store, snapshot_fn=snap_fn, project=p,
                                     scene=p / "Assets/Scenes/00_Bootstrap.unity",
                                     mode="regression", replan=_replan).execute(t2)
            mark(f"execute#{len(run.attempts)+1}", "ok" if o2.items[0].status in
                 ("applied_unverified",) else "failed", o2.items[0].status)
            r3 = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "capability_run.py"),
                 "--project", str(p), "--project-id", a.project_id,
                 "--replay", a.replay, "--no-save"],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800)
            after_c, _, _ = candidates_from_latest(p, limit=12)
            after_k = {str(c.root_cause).split(":", 1)[-1] for c in after_c}
            tkey = str((hsel or {}).get("root_cause") or "").split(":", 1)[-1]
            gone = tkey not in after_k
            new = sorted(after_k - before_roots - {tkey})
            l2 = evaluate("regression", replay_once=lambda: {
                "protocol_ok": True, "target_gone": gone,
                "new_clusters": new, "guards_ok": True})
            ladder_status, target_gone, new_roots = l2.status, gone, new
            print("retry ladder:", l2.status, "capture:", (r3.stdout or "")[-120:])
            mark(f"verify#{attempt_no}", l2.status,
                 f"hyp={str((hsel or {}).get('root_cause',''))[:80]}")
            attempt_no = len(run.attempts) + 1
            run.repairs.append(RepairRecord(
                finding_id=target.finding_id, plan_id=np.playbook_id,
                files=np.touched_assets,
                compile_ok=(o2.items[0].status in ("applied", "applied_unverified")),
                runnable=None, verdict=l2.status, metric_delta=None,
                attempt_no=attempt_no, ticket_id=tid2, diff_hash=_diff_hash(np),
                stop_reason=l2.status))
            run.attempts.append({
                "attempt_no": attempt_no,
                "hypothesis": str((hsel or {}).get("root_cause") or ""),
                "plan_id": np.playbook_id, "diff_hash": _diff_hash(np),
                "ticket_id": tid2, "item_status": o2.items[0].status,
                "ladder": l2.status, "stop_reason": l2.status})
            retry_obs["ladder"] = l2.status
            save_run(run)
            return {"ladder": l2.status, "status": o2.items[0].status}

        res = run_loop("regression", _decide, _act, max_steps=max_steps)
        retry_stop = res.stop_reason
        print("agent loop:", res.stop_reason, res.steps, retry_obs)

    # 8) 最终可用性门禁：编译 + 启动冒烟
    ok_smoke = False
    compiled = False
    try:
        import capability_run as CR
        st = CR._compile(p)
        compiled = (not st.get("failed")
                    and st.get("status") in {"completed", "up_to_date"})
        ok_smoke = bool(CR._smoke(p).get("ok")) if compiled else False
    except Exception as e:  # noqa: BLE001
        print("usability error:", type(e).__name__, e)
    run.usability_ok = bool(compiled and ok_smoke)
    run.usability_note = ("最终可编译/可启动" if run.usability_ok
                          else "最终不可用：编译或启动失败")
    mark("usability", "ok" if run.usability_ok else "failed",
         f"compile={compiled} smoke={ok_smoke}")
    for r in run.repairs:
        if r.compile_ok:
            r.runnable = ok_smoke
    run.stop_reason = (f"ladder={ladder_status}"
                       + (f";loop={retry_stop}" if retry_stop else ""))
    save_run(run)

    # 7) 评分（与 Lab 同口径）
    paper = json.loads((ROOT / "runs" / "i8_lab" / "paper.json")
                       .read_text(encoding="utf-8"))
    score = score_run(paper, run)
    mark("score", "ok",
         f"tp={score['dimensions']['discovery'].get('tp')} "
         f"fn={score['dimensions']['discovery'].get('fn')}")
    save_chain()
    print(json.dumps(score, ensure_ascii=False, indent=1))
    # 9) 归纳总结：只提议入库，等用户在 UI 批/驳
    try:
        from upi.agents.summarizer import SummarizerProposer
        print("label proposals:", SummarizerProposer(a.project_id).propose(run))
    except Exception as e:  # noqa: BLE001
        print("summarizer error:", type(e).__name__, e)
    return 0


def _diff_hash(plan) -> str:
    payload = json.dumps(plan.model_dump().get("diffs") or [],
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _make_collector(project: Path, project_id: str, replay: str):
    """考试侧证据采集器：source 直接读；replay_window 当场重采一次。"""
    from upi.core.evidence_request import fulfill

    def collector(req):
        if req.kind in ("source", "callstack", "code_search", "ast_callgraph",
                        "asset_inventory", "reference_graph", "settings"):
            return fulfill(req, "regression", project).model_dump()
        if req.kind == "replay_window":
            tag = "evidence_req_" + time.strftime("%H%M%S")
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "capability_run.py"),
                 "--project", str(project), "--project-id", project_id,
                 "--replay", replay, "--no-save"],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800)
            from upi.core.evidence_diagnosis import candidates_from_latest
            cands, _b, _p = candidates_from_latest(project, limit=12)
            text = "; ".join(
                f"{c.root_cause} score={c.score}" for c in cands[:12])
            return {"available": bool(cands), "kind": "replay_window",
                    "text": text or "重采后无候选",
                    "payload": {"tag": tag,
                                "roots": [c.root_cause for c in cands]}}
        return {"available": False, "kind": req.kind,
                "reason": "考试采集器不支持该请求"}

    return collector


def _issue(issue_d: dict):
    from upi.core.models import Issue
    if "issue_model" in issue_d:
        return Issue(**issue_d["issue_model"])
    raise RuntimeError("issue_model missing")


if __name__ == "__main__":
    raise SystemExit(main())
