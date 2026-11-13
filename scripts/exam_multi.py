"""I16：多病例考试编排（队列驱动）。

先修 1 个病例，验证通过保留；失败只回滚该病例，再下一个。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import exam_live as X  # noqa: E402
from upi.agents import nodes as N  # noqa: E402
from upi.agents.llm_planner import plan_issue  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.findings import (RepairRecord, RunFindings, new_run_id,  # noqa: E402
                               save_run, score_run)
from upi.core.multi_repair import run_queue  # noqa: E402
from upi.core.repair_queue import RepairQueue  # noqa: E402
from upi.core.ticket import (BatchTicket, TicketItem, TicketStore,  # noqa: E402
                             _item_token, _token, approve, approve_item)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402

EDITOR = Path(r"D:\unity_editor\6000.6.7f1\Editor\Unity.exe")


def _unity(*args, timeout=120):
    import subprocess
    p = subprocess.run(["unity", "command", *args, "--project-path",
                        "test_project", "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": p.stdout[-300:]}


def ensure_ready(project: Path) -> None:
    """编辑器就绪是硬步骤：不能假设 unity open 返回就等于可用。"""
    import subprocess
    if not _unity("editor status", timeout=30).get("success"):
        print("editor not ready; starting direct")
        subprocess.Popen([str(EDITOR), "-projectPath", str(project),
                          "-acceptSoftwareTermsForThisRunOnly"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            time.sleep(4)
            if _unity("editor status", timeout=30).get("success"):
                break
        else:
            raise SystemExit("Unity 启动超时")
    _unity("scene open", "--path", "Assets/Scenes/00_Bootstrap.unity")
    from upi.tooling import probe_control as PC
    PC.install(project)
    _unity("editor.playmode start")
    time.sleep(4)
    print("editor ready / play")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--baselines", type=int, default=3)
    ap.add_argument("--max-cases", type=int, default=11)
    a = ap.parse_args()
    p = Path(a.project).resolve()
    run_id = new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ensure_ready(p)

    # 基线
    from upi.core.evidence_diagnosis import candidates_from_latest
    baseline_runs = []
    for i in range(max(1, a.baselines)):
        ok, _ = X._capture_once(p, a.project_id, a.replay, run_dir,
                                f"baseline#{i + 1}", two_pass=True, retries=1)
        if not ok:
            return 1
        cands, _, _ = candidates_from_latest(p, limit=12)
        baseline_runs.append({str(c.root_cause).split(":", 1)[-1]
                              for c in cands})
        last_cands = cands
        print("baseline", i + 1, "roots", len(baseline_runs[-1]))
    (run_dir / "baselines.json").write_text(
        json.dumps([sorted(x) for x in baseline_runs], ensure_ascii=False,
                   indent=2), encoding="utf-8")

    # 诊断
    collector = X._make_collector(p, a.project_id, a.replay)
    from upi.core.project_store import knowledge_db as _knowledge_db
    nd = N.make_nodes(run_dir, live_llm=True,
                      knowledge_db=_knowledge_db(a.project_id),
                      mode="regression", evidence_collector=collector)
    d = nd["detect"]({"project": str(p)})
    if d.get("route") != "proceed":
        print("detect failed", d)
        return 1
    g = nd["diagnose"]({"issue": d["issue"], "project": str(p)})
    if g.get("route") != "proceed":
        print("diagnose failed", g)
        return 1
    hs = g.get("hypotheses") or []
    findings = X.findings_from_hypotheses(run_id, hs, {})
    fmap = {str((f.attribution.factors or {}).get("root_cause", "")): f
            for f in findings}
    run = RunFindings(run_id=run_id, project_id=a.project_id,
                      mode="regression", created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                      findings=findings, repairs=[])
    save_run(run)

    cfg = N.load_config(ROOT / "llm_config.yaml")
    cards = {}
    for c in (last_cands or []):
        spans = "; ".join(
            f"{s.file}:{s.start_line}-{s.end_line}({s.symbol})"
            for s in (c.sources or [])[:3])
        spans = spans.replace(str(p), "").replace(Path(p).name, "project")
        cards[str(c.root_cause)] = (
            f"mechanism={c.mechanism}; kind={c.kind}; score={c.score:.2f} "
            f"(CI {c.score_interval_low:.2f}-{c.score_interval_high:.2f}); "
            f"evidence_n={c.evidence_n}; spans={spans or 'unknown'}")
    plan_errors: dict[str, dict] = {}
    queue = RepairQueue(max_cases=a.max_cases)
    by_root = {}
    for i, h in enumerate(hs):
        root = str(h.get("root_cause") or "")
        if not root:
            continue
        fid = fmap.get(root).finding_id if fmap.get(root) else f"llm-{run_id}-{i}"
        st = queue.add(root, fid)
        by_root[root] = st
    plans, bases, hits = {}, {}, {}
    store = TicketStore()

    def plan(case):
        h = next((x for x in hs if str(x.get("root_cause")) == case.case_id), None)
        if h is None:
            return False
        pl, meta = plan_issue(cfg, X._issue(d["issue"]), h, p,
                              candidate_card=cards.get(case.case_id, ""),
                              remaining_sites=case.remaining_sites)
        print("plan", case.case_id, bool(pl), meta.get("error", ""))
        plan_errors[case.case_id] = meta
        if pl is None:
            return False
        plans[case.case_id] = pl
        return True

    def apply(case):
        pl = plans.get(case.case_id)
        if pl is None:
            return False
        bases[case.case_id] = X._git_head(p)
        tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-" + str(len(plans))
        iid = "it-1"
        it = TicketItem(item_id=iid, cluster_id="llm", metric="llm",
                        shape="llm_plan", kind="llm", count=1,
                        pattern_id="llm", playbook_id=pl.playbook_id,
                        permission_tier=pl.permission_tier,
                        plan=pl.model_dump(), status="pending",
                        item_approve_token=_item_token(tid, iid))
        t = BatchTicket(ticket_id=tid, project_id="paper", mode="regression",
                        created_at=E.now(), status="awaiting_approval",
                        items=[it], approve_token=_token(tid, [it]))
        store.save(t)
        approve(t, approver="user", note="用户授权：全部批准",
                token=t.approve_token)
        if it.permission_tier != "auto":
            approve_item(t, iid, approver="user",
                         note="用户授权：全部批准",
                         token=it.item_approve_token)
        store.save(t)
        out = UnityTicketExecutor(
            store, snapshot_fn=lambda *args: SN.snapshot(*args,
                                                          root=Path(p).resolve()),
            project=p, scene=p / "Assets/Scenes/00_Bootstrap.unity",
            mode="regression").execute(t)
        case.ticket_id = tid
        return out.items[0].status in ("applied", "applied_unverified")

    def verify(case):
        ok, _ = X._capture_once(p, a.project_id, a.replay, run_dir,
                                f"post-{case.case_id[:40]}", retries=1)
        if not ok:
            return "unverified"
        cands, _, _ = candidates_from_latest(p, limit=12)
        now = {str(c.root_cause).split(":", 1)[-1] for c in cands}
        target = case.case_id.split(":", 1)[-1]
        gone = target not in now
        new = sorted(now - set().union(*baseline_runs) - {target})
        from upi.core.site_audit import sites_remaining
        sa = sites_remaining(p, [{"find": d.get("find", ""),
                                  "replace": d.get("replace", "")}
                                 for d in plans[case.case_id].diffs])
        from upi.core.verification_v2 import evaluate_v2
        v = evaluate_v2(target_gone=gone, new_clusters=new, guards_ok=True,
                        sites_remaining=sa)
        case.remaining_sites = [f"{pat}: {', '.join(files)}"
                                for pat, files in sa.items()]
        print("verify", case.case_id, v.status, v.reason)
        from upi.core.repair_loop import Observation
        import hashlib
        pl = plans.get(case.case_id)
        dh = hashlib.sha256(
            json.dumps(pl.model_dump() if pl else {}, sort_keys=True,
                       ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        return Observation(
            verdict=v.status, target_gone=gone, new_clusters=new,
            sites_remaining=sum(len(x) for x in sa.values()),
            reason=v.reason, diff_hash=dh)

    def rollback(case):
        return X._git_reset(p, bases.get(case.case_id, ""))

    res = run_queue(queue, plan=plan, apply=apply, verify=verify,
                    rollback=rollback, max_cases=a.max_cases)
    print("queue", res.summary())
    (run_dir / "plan_meta.json").write_text(
        json.dumps(plan_errors, ensure_ascii=False, indent=2), encoding="utf-8")

    for case in queue.cases:
        pl = plans.get(case.case_id)
        run.repairs.append(RepairRecord(
            finding_id=case.finding_id, plan_id=(pl.playbook_id if pl else ""),
            files=(pl.touched_assets if pl else []),
            compile_ok=bool(pl), verdict=case.status,
            attempt_no=case.attempts, ticket_id=case.ticket_id,
            stop_reason=case.reason))
    run.stop_reason = "queue:" + json.dumps(res.summary(), ensure_ascii=False)
    save_run(run)
    paper = json.loads((ROOT / "runs" / "i8_lab" / "paper.json")
                       .read_text(encoding="utf-8"))
    print(json.dumps(score_run(paper, run), ensure_ascii=False, indent=1))
    print("elapsed", round(time.time() - t0, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
