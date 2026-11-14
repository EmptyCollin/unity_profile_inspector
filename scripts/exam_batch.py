"""I19：机制合并批量考试驱动（单轮先跑通）。

N findings → M 机制单元 → M 补丁 → 全部应用 → 编译一次 → 一次 capture → 验全部。
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
import exam_multi as M  # noqa: E402
from upi.agents import nodes as N  # noqa: E402
from upi.agents.llm_planner import plan_issue  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.batch_round import run_batch  # noqa: E402
from upi.core.evidence_diagnosis import candidates_from_latest  # noqa: E402
from upi.core.findings import (RepairRecord, RunFindings, new_run_id,  # noqa: E402
                               save_run, score_run)
from upi.core.mechanism_batch import PatchRef, consolidate  # noqa: E402
from upi.core.ticket import (BatchTicket, TicketItem, TicketStore,  # noqa: E402
                             _item_token, _token, approve, approve_item)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402


def latest_counters() -> dict:
    files = sorted((ROOT / "runs").glob("*/counters.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--baselines", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    p = Path(a.project).resolve()
    run_id = new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    M.ensure_ready(p)

    baseline_roots = set()
    baseline_metrics: dict = {}

    def capture_with_recovery(label: str, *, two_pass: bool):
        ok, out = X._capture_once(p, a.project_id, a.replay, run_dir,
                                  label, two_pass=two_pass, retries=1)
        if ok:
            return True, out
        print("capture failed; restart editor and retry", label)
        try:
            M.ensure_ready(p)
        except Exception as e:  # noqa: BLE001
            print("editor restart failed", type(e).__name__, e)
            return False, out
        return X._capture_once(p, a.project_id, a.replay, run_dir,
                               label + "-retry", two_pass=two_pass,
                               retries=1)

    for i in range(max(1, a.baselines)):
        ok, _ = capture_with_recovery(f"baseline#{i + 1}", two_pass=True)
        if not ok:
            return 1
        mp = run_dir / "merged_candidates.json"
        if mp.exists():
            roots = {str(x.get("root_cause", "")).split(":", 1)[-1]
                     for x in json.loads(mp.read_text(encoding="utf-8"))}
        else:
            cands, _, _ = candidates_from_latest(p, limit=12)
            roots = {str(c.root_cause).split(":", 1)[-1] for c in cands}
        baseline_roots |= roots
        print("baseline", i + 1, "roots", len(baseline_roots))

    baseline_metrics = latest_counters()
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
    # I20：静态机制站点补检（运行时不热的漏洞）
    from upi.core.mechanism_inventory import scan as _scan_sites
    from upi.core.findings import Anchor, AttributionInfo, ToolFinding
    existing_methods = {a.method for f in findings for a in f.anchors if a.method}
    for mech, sites in _scan_sites(p).by_mechanism().items():
        for s in sites:
            if not s.method or s.method in existing_methods:
                continue
            root = f"hot_function:{Path(s.file).stem}.{s.method}"
            if any(root == str((f.attribution.factors or {}).get("root_cause"))
                   for f in findings):
                continue
            findings.append(ToolFinding(
                finding_id=f"static-{run_id}-{len(findings)}",
                mechanism=root, metric="", link="",
                anchors=[Anchor(file=s.file, method=s.method,
                                start_line=s.line, end_line=s.line)],
                evidence_refs=[], state="candidate",
                attribution=AttributionInfo(confidence=0.5,
                                            factors={"root_cause": root})))
            existing_methods.add(s.method)
            print("static site added", root, s.file, s.line)
    run = RunFindings(run_id=run_id, project_id=a.project_id,
                      mode="regression", created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                      findings=findings, repairs=[])
    save_run(run)
    units = consolidate([f.model_dump() for f in findings])
    print("findings", len(findings), "units", len(units))

    cfg = N.load_config(ROOT / "llm_config.yaml")
    store = TicketStore()
    plans, bases = {}, {}
    round_no = 1
    after_metrics: dict = {}

    def plan(unit):
        h = next((x for x in hs
                  if str(x.get("root_cause")) in unit.targets
                  or str(x.get("root_cause")) == unit.mechanism), None)
        if h is None and unit.targets:
            anchors = [s for s in unit.sites if "::" in s]
            h = {"root_cause": unit.targets[0], "code_anchors": anchors[:4]}
        if h is None:
            return None
        pl, meta = plan_issue(cfg, X._issue(d["issue"]), h, p,
                              strategy=f"round{round_no}")
        print("plan", unit.unit_id, unit.mechanism, bool(pl), meta.get("error", ""))
        if pl is None:
            return None
        plans[unit.unit_id] = pl
        bases[unit.unit_id] = X._git_head(p)
        return PatchRef(unit.unit_id, list(pl.touched_assets))

    def apply(patch):
        pl = plans.get(patch.unit_id)
        if pl is None:
            return False
        tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-" + patch.unit_id
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
            store, snapshot_fn=lambda *args: SN.snapshot(*args, root=p),
            project=p, scene=p / "Assets/Scenes/00_Bootstrap.unity",
            mode="regression").execute(t)
        return out.items[0].status in ("applied", "applied_unverified")

    def compile_fn():
        import capability_run as CR
        st = CR._compile(p)
        ok = (not st.get("failed")
              and st.get("status") in {"completed", "up_to_date"})
        print("compile", ok, st.get("status"))
        return ok

    def capture_fn():
        nonlocal after_metrics
        ok, _ = capture_with_recovery("batch-after", two_pass=False)
        if not ok:
            raise RuntimeError("batch capture failed")
        after_metrics = latest_counters()
        cands, _, _ = candidates_from_latest(p, limit=12)
        return {str(c.root_cause).split(":", 1)[-1] for c in cands}

    def verify(unit, obs):
        target = unit.targets[0].split(":", 1)[-1] if unit.targets else unit.mechanism
        now = set(obs or [])
        gone = target not in now
        new = sorted(now - baseline_roots - {target})
        from upi.core.site_audit import sites_remaining
        pl = plans[unit.unit_id]
        sa = sites_remaining(p, [{"file": d.get("file", ""),
                                  "find": d.get("find", ""),
                                  "replace": d.get("replace", "")}
                                 for d in pl.diffs])
        from upi.core.verification_v2 import evaluate_v2
        # I20：先用 metric delta + 机制不变量；缺失才退回 cluster
        if baseline_metrics and after_metrics:
            from upi.core.mechanism_checks import invariant_ok as _inv
            from upi.core.targets import evaluate as _eval_target
            inv_ok, _inv_reason = _inv(p, unit.mechanism)
            _spec, _obs, _tv = _eval_target(
                {"finding_id": unit.unit_id, "mechanism": unit.mechanism},
                baseline_metrics, after_metrics,
                invariant_ok=inv_ok,
                sites_remaining=sum(len(x) for x in sa.values()))
            print("metric-verify", unit.unit_id, _tv.status,
                  _tv.delta, _tv.threshold)
            if _tv.status != "unmeasured":
                return _tv.status
        v = evaluate_v2(target_gone=gone, new_clusters=new, guards_ok=True,
                        sites_remaining=sa)
        print("verify", unit.unit_id, target, v.status)
        return v.status

    def rollback(patch):
        return X._git_reset(p, bases.get(patch.unit_id, ""))

    active = list(units)
    all_verified: set[str] = set()
    for r in range(1, max(1, a.rounds) + 1):
        round_no = r
        if not active:
            break
        res = run_batch(active, plan=plan, apply=apply, compile_fn=compile_fn,
                        capture=capture_fn, verify=verify, rollback=rollback,
                        round_no=r)
        print("batch", r, res)
        all_verified.update(res.verified)
        for u in active:
            pl = plans.get(u.unit_id)
            run.repairs.append(RepairRecord(
                finding_id=(u.finding_ids[0] if u.finding_ids else ""),
                plan_id=(pl.playbook_id if pl else ""),
                files=(pl.touched_assets if pl else []),
                compile_ok=res.compile_ok and bool(pl),
                verdict=("verified" if u.unit_id in res.verified
                         else res.failed.get(u.unit_id, "not_run")),
                attempt_no=r, stop_reason=res.failed.get(u.unit_id, "")))
        failed = set(res.failed) - {"__compile__", "__capture__"}
        active = [u for u in units if u.unit_id in failed]
        if not active:
            break
    run.stop_reason = f"batch:verified={len(all_verified)}"
    save_run(run)
    paper = json.loads((ROOT / "runs" / "i8_lab" / "paper.json")
                       .read_text(encoding="utf-8"))
    print(json.dumps(score_run(paper, run), ensure_ascii=False, indent=1))
    print("elapsed", round(time.time() - t0, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
