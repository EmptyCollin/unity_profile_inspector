"""真实考试驱动：live LLM 诊断/规划 → 工单 → 执行 → 记账 → 评分。

这是“监考”脚本，不是产品路径；它只调用产品节点与工单模型，
不向 LLM 注入任何测试/答案信息（提示词筛查在 factor_judge/llm_planner 内）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.agents import nodes as N  # noqa: E402
from upi.adapters.devsim import DevSimAdapter  # noqa: E402
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
                                        factors={"root_cause": h.get("root_cause"),
                                                 "window_id": h.get("window_id", "")}),
            state="candidate"))
    return out


def _git_head(project: Path) -> str:
    p = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def _git_reset(project: Path, ref: str) -> bool:
    if not ref:
        return False
    p = subprocess.run(["git", "-C", str(project), "reset", "--hard", ref],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode == 0


def _log_tool_error(run_dir: Path, stage: str, exc: BaseException) -> None:
    import traceback
    rec = {"label": stage, "error": f"{type(exc).__name__}: {exc}",
           "traceback": traceback.format_exc()[-3000:]}
    try:
        with (run_dir / "tool_errors.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _capture_once(project: Path, project_id: str, replay: str,
                  run_dir: Path, label: str, *,
                  two_pass: bool = False, retries: int = 1,
                  checkpoint=None, light: bool = False,
                  fast: bool = False, target_windows: bool = False):
    """采集子进程；失败落 tool_errors.jsonl 并重试一次。返回 (ok, out)。"""
    if checkpoint is not None and checkpoint.done(label):
        if (run_dir / "merged_candidates.json").exists():
            print(f"capture resume: skip {label}")
            return True, None
    last = None
    for attempt in range(retries + 1):
        if os.environ.get("UPI_SIMULATE_CAPTURE_FAIL") == label:
            err = {"label": label, "attempt": attempt + 1,
                   "returncode": -99, "stdout": "", "stderr": "simulated failure"}
            try:
                with (run_dir / "tool_errors.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(err, ensure_ascii=False) + "\n")
            except OSError:
                pass
            if attempt < retries:
                time.sleep(0.5)
            continue
        try:
            ok, last = DevSimAdapter(project).capture_replay(
                replay, run_dir, label,
                two_pass=two_pass, light=light, fast=fast,
                target_windows=target_windows)
            if ok:
                if checkpoint is not None:
                    try:
                        checkpoint.save(step=label, status="done")
                    except Exception:  # noqa: BLE001
                        pass
                return True, last
            err = {"label": label, "attempt": attempt + 1,
                   "returncode": getattr(last, "returncode", -1),
                   "stdout": (getattr(last, "stdout", "") or "")[-2000:],
                   "stderr": (getattr(last, "stderr", "") or "")[-2000:]}
        except Exception as e:  # noqa: BLE001
            err = {"label": label, "attempt": attempt + 1,
                   "returncode": -1,
                   "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
        try:
            with (run_dir / "tool_errors.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(err, ensure_ascii=False) + "\n")
        except OSError:
            pass
        from upi.core.resilience import classify_error
        kind = classify_error((err.get("stderr") or "") + (err.get("stdout") or ""))
        if kind == "deterministic":
            break
        if attempt < retries:
            time.sleep(1.5)
    return False, last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--baselines", type=int, default=3,
                    help="修复前同协议基线次数（<2 则无法 verified）")
    ap.add_argument("--resume", default="",
                    help="从已有 run_id 续跑（跳过已完成的采集）")
    a = ap.parse_args()
    p = Path(a.project).resolve()
    run_id = a.resume or new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    from upi.core.resilience import Checkpoint
    cp = Checkpoint(run_dir)
    chain: list[dict] = []
    prior_chain: list[dict] = []
    base_head = ""
    try:
        _cp = run_dir / "chain.json"
        if _cp.exists():
            prior_chain = list(json.loads(_cp.read_text(encoding="utf-8"))
                               .get("steps", []))
    except Exception:  # noqa: BLE001
        prior_chain = []
    t_start = time.time()
    baseline_meta: dict = {"ran": False, "count": 0, "hits": 0,
                           "reason": "尚未运行基线"}

    def mark(step: str, status: str = "ok", detail: str = "") -> None:
        chain.append({"step": step, "status": status,
                      "at_s": round(time.time() - t_start, 2),
                      "detail": detail[:200]})

    def save_chain() -> None:
        executes = [s for s in chain if str(s.get("step", "")).startswith("execute#")]
        # 合并连续 resume 标记，避免同一次复用显示三行
        merged: list[dict] = []
        for s in chain:
            if s.get("step") == "resume" and merged and merged[-1].get("step") == "resume":
                prev = merged[-1]
                detail = str(prev.get("detail") or "")
                add = str(s.get("detail") or "")
                if add and add not in detail:
                    prev["detail"] = (detail + " · " + add)[:200]
                prev["status"] = s.get("status", prev.get("status"))
                continue
            merged.append(dict(s))
        # 续跑时保留历史步骤；重名步骤加“续跑”后缀，不覆盖旧记录
        combined: list[dict] = []
        seen_names = set()
        for s in prior_chain:
            item = dict(s)
            item["step"] = str(item.get("step", "")) + "（历史）"
            combined.append(item)
            seen_names.add(str(s.get("step", "")))
        for s in merged:
            item = dict(s)
            name = str(item.get("step", ""))
            if name in seen_names:
                item["step"] = name + "（续跑）"
            combined.append(item)
        (run_dir / "chain.json").write_text(json.dumps(
            {"steps": combined,
             "backtracks": max(0, len(executes) - 1),
             "baseline": baseline_meta},
            ensure_ascii=False, indent=2), encoding="utf-8")

    # 1) 修复前基线：同协议 replay 跑 N 次（默认 3）；resume 可跳过
    from upi.core.evidence_diagnosis import candidates_from_latest
    baseline_runs: list[dict] = []
    resume_state = cp.load() if a.resume else {}
    if resume_state.get("baseline_runs"):
        baseline_runs = list(resume_state["baseline_runs"])
        mark("resume", "ok",
             f"跳过基线，复用 {len(baseline_runs)} 次")
    else:
        n_base = max(1, int(a.baselines))
        for bi in range(n_base):
            okcap, r = _capture_once(p, a.project_id, a.replay, run_dir,
                                     f"baseline#{bi + 1}", two_pass=True,
                                     retries=1, checkpoint=cp)
            if not okcap:
                mark(f"baseline#{bi + 1}", "failed",
                     (r.stdout or r.stderr)[-160:] if r else "capture failed")
                save_chain()
                return 1
            _cands, _, _ = candidates_from_latest(p, limit=12)
            roots = sorted({str(c.root_cause).split(":", 1)[-1] for c in _cands})
            baseline_runs.append({"index": bi, "roots": roots})
            mark(f"baseline#{bi + 1}", "ok", f"roots={len(roots)}")
            print(f"baseline #{bi + 1}: roots={len(roots)}")
    before_roots0 = set(baseline_runs[-1]["roots"])
    baseline_meta.update({"ran": True, "count": len(baseline_runs),
                          "reason": f"已跑 {len(baseline_runs)} 次同协议基线"})
    (run_dir / "baselines.json").write_text(json.dumps(
        baseline_runs, ensure_ascii=False, indent=2), encoding="utf-8")
    cp.save(step="baselines", status="done", data={"baseline_runs": baseline_runs})

    resume_eval = bool(resume_state.get("plan")
                       and (run_dir / "findings.json").exists())
    if resume_eval:
        from upi.core.findings import load_run
        from upi.core.models import EditPlan
        run = load_run(run_id)
        findings = run.findings
        hs = resume_state.get("hypotheses") or []
        plan = EditPlan(**resume_state["plan"])
        plan_hyp = resume_state.get("plan_hyp") or {}
        mark("resume", "ok", "复用 diagnose/plan，跳过重复 LLM")
    else:
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
        cp.save(step="detect", status="done", data={"issue": d["issue"]})
        g = nd["diagnose"]({"issue": d["issue"], "project": str(p)})
        mark("diagnose", g.get("route", ""),
             f"tokens={g.get('tokens_used')} hyps={len(g.get('hypotheses') or [])}")
        print("diagnose:", g.get("route"), g.get("diagnose_source"),
              "tokens", g.get("tokens_used"))
        if g.get("route") != "proceed":
            print("diagnose error:", g.get("error"))
            save_chain()
            return 1
        cp.save(step="diagnose", status="done",
                data={"hypotheses": [h for h in (g.get("hypotheses") or [])]})
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
        cp.save(step="plan", status="done",
                data={"plan": plan.model_dump(),
                      "plan_hyp": (plan_hyp or {})})
    target_key0 = str((plan_hyp or {}).get("root_cause") or "").split(":", 1)[-1]
    baseline_hits = sum(1 for b in baseline_runs
                        if target_key0 in set(b["roots"]))
    baseline_ok = baseline_hits >= 2 and len(baseline_runs) >= 3
    baseline_meta.update({"hits": baseline_hits, "target": target_key0,
                          "ok": baseline_ok})
    (run_dir / "baselines.json").write_text(json.dumps(
        {"runs": baseline_runs, "target": target_key0,
         "hits": baseline_hits, "ok": baseline_ok},
        ensure_ascii=False, indent=2), encoding="utf-8")
    resume_verify_only = bool(resume_eval and cp.done("execute#1"))

    if resume_verify_only:
        out = SimpleNamespace(status="done", items=[SimpleNamespace(
            status=str(cp.load().get("item_status", "applied_unverified")),
            error="")])
        mark("resume", "ok", "verify-only：复用执行结果，不重复 apply")
    else:
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
        base_head = _git_head(p)
        out = ex.execute(t)
        mark("execute#1", "ok" if out.items[0].status in
             ("applied_unverified",) else "failed",
             f"{out.items[0].status} {out.items[0].error[:80]}")
        cp.save(step="execute#1", status="done",
                data={"item_status": out.items[0].status})
    print("execute:", out.status)
    for it in out.items:
        print(" item:", it.status, it.error[:100])

    # 6) 修复后 replay 一次：目标还在？出现新问题？（先不烧 3 次基线）
    ladder_status, target_gone, new_roots = "unverified", None, []
    ladder_stop_reason = ""
    try:
        from upi.core.verify_ladder import evaluate as _ladder
        before_roots = before_roots0
        # 修复后再采一次（同 replay、同协议）；失败重试一次，仍失败降级 unverified
        okcap, r2 = _capture_once(p, a.project_id, a.replay, run_dir,
                                  "post-capture#1", two_pass=False, retries=1)
        print("post-capture:", ((r2.stdout or r2.stderr)[-160:]
                                if r2 is not None else "failed"))
        if not okcap:
            ladder_status = "unverified"
            ladder_stop_reason = "tool_error"
            mark("verify#1", "unverified", "post-capture 失败（已重试）；降级 unverified")
            raise RuntimeError("post-capture unavailable")
        after_cands, _, _ = candidates_from_latest(p, limit=12)
        after_roots = {str(c.root_cause).split(":", 1)[-1] for c in after_cands}
        target_rc = str((plan_hyp or {}).get("root_cause") or "").split(":", 1)[-1]
        target_gone = target_rc not in after_roots
        new_roots = sorted(after_roots - before_roots - {target_rc})
        ladder = _ladder("regression", replay_once=lambda: {
            "protocol_ok": True, "target_gone": target_gone,
            "new_clusters": new_roots, "guards_ok": True},
            baselines=lambda: baseline_ok)
        ladder_status = ladder.status
        ladder_stop_reason = ladder.status
        print("ladder:", ladder.status, ladder.reason)
        mark("verify#1", ladder.status, ladder.reason[:160])
        cp.save(step="verify#1", status="done",
                data={"ladder": ladder.status})
        (run_dir / "verify_ladder.json").write_text(
            ladder.model_dump_json(indent=2), encoding="utf-8")
        (run_dir / "verify_ladder_attempt1.json").write_text(
            ladder.model_dump_json(indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("ladder error:", type(e).__name__, e)

    # 7) 受控 Agent loop：replay 一次不干净 → 换方案再试一次（regression 预算内）
    target = findings[0] if findings else None
    if target is not None and not resume_verify_only:
        run.repairs.append(RepairRecord(
            finding_id=target.finding_id, plan_id=plan.playbook_id,
            files=plan.touched_assets,
            compile_ok=(out.items[0].status in ("applied", "applied_unverified")),
            runnable=None, verdict=ladder_status,
            attempt_no=1, ticket_id=t.ticket_id,
            diff_hash=_diff_hash(plan),
            stop_reason=(ladder_stop_reason or ladder_status)))
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
    relation = "or"
    site_count = 0
    max_steps = max_attempts("regression")
    if (ladder_status == "needs_recheck" and target is not None
            and not resume_verify_only):
        from upi.core.agent_loop import run_loop

        def _decide(state):
            last = state.get("last") or {}
            if retry_round > 0 and last.get("ladder") not in ("needs_recheck", None):
                return {"stop": True,
                        "reason": f"ladder:{last.get('ladder')}"}
            if retry_round >= max_steps:
                return {"stop": True, "reason": "budget"}
            from upi.core.repair_protocol import ProtocolState, decide_next
            action = decide_next(ProtocolState(
                relation, retry_round, max_steps,
                sites_remaining=site_count))
            if action in ("stop", "rollback_and_stop"):
                return {"stop": True, "reason": f"protocol:{action}"}
            return {"tool": "plan_patch"}

        def _act(_decision):
            nonlocal ladder_status, target_gone, new_roots, retry_round
            nonlocal relation, site_count
            retry_round += 1
            # I15：互斥假设，重试前先回滚上一次未验证补丁（OR 协议）
            if base_head:
                rok = _git_reset(p, base_head)
                mark("rollback#1", "ok" if rok else "failed",
                     "重试前回滚首个未验证补丁" if rok else "git reset 失败")
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
            attempt_no = len(run.attempts) + 1
            okcap, r3 = _capture_once(p, a.project_id, a.replay, run_dir,
                                      f"post-capture#retry", two_pass=False,
                                      retries=1)
            if not okcap:
                attempt_no = len(run.attempts) + 1
                run.repairs.append(RepairRecord(
                    finding_id=target.finding_id, plan_id=np.playbook_id,
                    files=np.touched_assets,
                    compile_ok=(o2.items[0].status in
                                ("applied", "applied_unverified")),
                    runnable=None, verdict="unverified", metric_delta=None,
                    attempt_no=attempt_no, ticket_id=tid2,
                    diff_hash=_diff_hash(np), stop_reason="tool_error"))
                run.attempts.append({
                    "attempt_no": attempt_no,
                    "hypothesis": str((hsel or {}).get("root_cause") or ""),
                    "plan_id": np.playbook_id, "ticket_id": tid2,
                    "item_status": o2.items[0].status,
                    "ladder": "unverified", "stop_reason": "tool_error"})
                save_run(run)
                mark(f"verify#{attempt_no}", "unverified",
                     "post-capture 失败（已重试）；降级 unverified，已记账")
                retry_obs["ladder"] = "unverified"
                return {"ladder": "unverified"}
            after_c, _, _ = candidates_from_latest(p, limit=12)
            after_k = {str(c.root_cause).split(":", 1)[-1] for c in after_c}
            tkey = str((hsel or {}).get("root_cause") or "").split(":", 1)[-1]
            gone = tkey not in after_k
            new = sorted(after_k - before_roots - {tkey})
            retry_hits = sum(1 for b in baseline_runs
                             if tkey in set(b["roots"]))
            l2 = evaluate("regression", replay_once=lambda: {
                "protocol_ok": True, "target_gone": gone,
                "new_clusters": new, "guards_ok": True},
                baselines=lambda: (retry_hits >= 2
                                   and len(baseline_runs) >= 3))
            ladder_status, target_gone, new_roots = l2.status, gone, new
            try:
                from upi.core.site_audit import sites_remaining as _sr
                _sa = _sr(p, [{"find": d.get("find", ""),
                               "replace": d.get("replace", "")}
                              for d in np.diffs])
                site_count = sum(len(v) for v in _sa.values())
                if site_count:
                    relation = "one"
                (run_dir / f"site_audit_attempt{attempt_no}.json").write_text(
                    json.dumps(_sa, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except Exception:  # noqa: BLE001
                site_count = 0
            print("retry ladder:", l2.status, "capture:", (r3.stdout or "")[-120:])
            (run_dir / f"verify_ladder_attempt{attempt_no}.json").write_text(
                l2.model_dump_json(indent=2), encoding="utf-8")
            mark(f"verify#{attempt_no}", l2.status,
                 f"hyp={str((hsel or {}).get('root_cause',''))[:80]}")
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
                "ladder": l2.status, "stop_reason": l2.status,
                "relation": relation, "sites_remaining": site_count})
            retry_obs["ladder"] = l2.status
            save_run(run)
            return {"ladder": l2.status, "status": o2.items[0].status}

        res = run_loop("regression", _decide, _act, max_steps=max_steps,
                       on_error=lambda s, e: _log_tool_error(run_dir, s, e))
        retry_stop = res.stop_reason
        if retry_stop == "tool_error":
            _n = len(run.attempts) + 1
            run.repairs.append(RepairRecord(
                finding_id=(target.finding_id if target else ""),
                plan_id=plan.playbook_id, files=plan.touched_assets,
                compile_ok=False, runnable=None, verdict="unverified",
                attempt_no=_n, stop_reason="tool_error"))
            run.attempts.append({
                "attempt_no": _n, "hypothesis": "",
                "plan_id": plan.playbook_id, "ticket_id": "",
                "item_status": "unknown", "ladder": "unverified",
                "stop_reason": "tool_error"})
            save_run(run)
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
    if ladder_status != "verified" and base_head:
        if _git_reset(p, base_head):
            mark("rollback-final", "ok", "未 verified：回滚全部未验证补丁")
        else:
            mark("rollback-final", "failed", "最终回滚失败")
    try:
        from upi.core.site_audit import sites_remaining
        sa = sites_remaining(p, [{"find": d.get("find", ""),
                                  "replace": d.get("replace", "")}
                                 for d in (plan.diffs if plan else [])])
        (run_dir / "site_audit.json").write_text(
            json.dumps(sa, ensure_ascii=False, indent=2), encoding="utf-8")
        from upi.core.verification_v2 import evaluate_v2
        v2 = evaluate_v2(
            target_gone=bool(target_gone),
            new_clusters=list(new_roots or []),
            guards_ok=True,
            sites_remaining=sa,
            baselines_ok=(ladder_status == "verified"))
        (run_dir / "verification_v2.json").write_text(
            json.dumps(v2.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8")
        if v2.status != ladder_status:
            ladder_status = v2.status
            mark("verify-v2", v2.status, v2.reason)
        else:
            mark("site-audit", "ok", f"剩余站点={sum(len(v) for v in sa.values())}")
    except Exception as e:  # noqa: BLE001
        mark("site-audit", "failed", f"{type(e).__name__}: {e}")
    run.usability_ok = bool(compiled and ok_smoke)
    run.usability_note = ("最终可编译/可启动" if run.usability_ok
                          else "最终不可用：编译或启动失败")
    mark("usability", "ok" if run.usability_ok else "failed",
         f"compile={compiled} smoke={ok_smoke}")
    if resume_verify_only and run.repairs:
        run.repairs[-1].verdict = ladder_status
        run.repairs[-1].stop_reason = ladder_stop_reason or ladder_status
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
    cp.save(step="score", status="done")
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
                 "--replay", replay, "--no-save", "--light",
                 "--target-windows"],
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
