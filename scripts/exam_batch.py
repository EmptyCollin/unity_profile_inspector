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
from upi.adapters.loader import load_adapter  # noqa: E402
from upi.agents.flow_run import FlowRun  # noqa: E402
from upi.agents.llm_planner import plan_issue  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.batch_round import run_batch  # noqa: E402
from upi.core.evidence_diagnosis import candidates_from_latest  # noqa: E402
from upi.core.findings import (RepairRecord, RunFindings, new_run_id,  # noqa: E402
                               save_run, score_run)
from upi.core.mechanism_batch import PatchRef, consolidate  # noqa: E402
from upi.core.mechanism_infer import count_in_method  # noqa: E402
from upi.core.plan_consensus import choose_consensus, plan_key  # noqa: E402
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


def counters_since(start_ts: float) -> dict:
    """合并 start_ts 之后所有 capture 的 counters series。"""
    merged: dict[str, dict] = {}
    for p in (ROOT / "runs").glob("*/counters.json"):
        try:
            if p.stat().st_mtime < start_ts:
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for name, node in (d.get("counters") or {}).items():
            series = node.get("series") if isinstance(node, dict) else None
            if not isinstance(series, list):
                continue
            slot = merged.setdefault(name, {"series": []})
            slot["series"].extend(float(x) for x in series)
    return {"counters": merged}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--baselines", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--max-units", type=int, default=0)
    ap.add_argument("--targets", default="",
                    help="逗号分隔的 mechanism 子串，只跑匹配的单元")
    ap.add_argument("--plan-repeat", type=int, default=1,
                    help="同一输入重复 plan 次数，用于统计 LLM 一致性")
    ap.add_argument("--plan-min-votes", type=int, default=2,
                    help="重复采样时判定稳定所需最少同票")
    ap.add_argument("--llm-decide", action="store_true",
                    help="decide 节点由 LLM 选择 retry/stop（失败退回规则）")
    ap.add_argument("--adapter", default="upi.adapters.devsim:DevSimAdapter",
                    help="项目适配器 module:Class")
    ap.add_argument("--static-repair", action="store_true",
                    help="把静态-only 候选也纳入修复（默认只作 potential）")
    a = ap.parse_args()
    p = Path(a.project).resolve()
    run_id = new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    flow = FlowRun(run_dir, run_id)
    flow.enter("baseline")
    t0 = time.time()
    M.ensure_ready(p)
    adapter = load_adapter(a.adapter, p)

    baseline_roots = set()
    baseline_metrics: dict = {}

    def capture_with_recovery(label: str, *, two_pass: bool,
                              light: bool = False, fast: bool = False,
                              target_windows: bool = False):
        ok, out = adapter.capture_replay(
            a.replay, run_dir, label, two_pass=two_pass, light=light,
            fast=fast, target_windows=target_windows)
        if ok:
            return True, out
        print("capture failed; restart editor and retry", label)
        try:
            M.ensure_ready(p)
        except Exception as e:  # noqa: BLE001
            print("editor restart failed", type(e).__name__, e)
            return False, out
        return adapter.capture_replay(
            a.replay, run_dir, label + "-retry", two_pass=two_pass,
            light=light, fast=fast, target_windows=target_windows)

    for i in range(max(1, a.baselines)):
        ok, _ = capture_with_recovery(
            f"baseline#{i + 1}", two_pass=False,
            light=True, target_windows=True)
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

    baseline_metrics = counters_since(t0)
    flow.enter("diagnose")
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
    # I20：静态机制站点补检只用于修复单元，不进入评分 findings
    from upi.core.mechanism_inventory import scan as _scan_sites
    static_findings: list[dict] = []
    existing_methods = {a.method for f in findings for a in f.anchors if a.method}
    added_mech: dict[str, int] = {}
    for mech, sites in _scan_sites(p).by_mechanism().items():
        if added_mech.get(mech, 0) >= 3:
            continue
        for s in sites:
            if not s.method or s.method in existing_methods:
                continue
            root = f"hot_function:{Path(s.file).stem}.{s.method}"
            if any(root == str((f.attribution.factors or {}).get("root_cause"))
                   for f in findings):
                continue
            static_findings.append({
                "finding_id": f"static-{run_id}-{len(static_findings)}",
                "mechanism": root, "metric": "", "link": "",
                "anchors": [{"file": s.file, "method": s.method,
                             "start_line": s.line, "end_line": s.line}],
                "evidence_refs": [], "state": "candidate",
                "attribution": {"confidence": 0.5,
                                "factors": {"root_cause": root}}})
            existing_methods.add(s.method)
            added_mech[mech] = added_mech.get(mech, 0) + 1
            print("static candidate", root, s.file, s.line)
            if added_mech[mech] >= 3:
                break
    run = RunFindings(run_id=run_id, project_id=a.project_id,
                      mode="regression", created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                      findings=findings, repairs=[])
    save_run(run)
    repair_inputs = [f.model_dump() for f in findings]
    if a.static_repair:
        repair_inputs += static_findings
    units = consolidate(repair_inputs)
    if a.targets:
        needles = {x.strip() for x in a.targets.split(",") if x.strip()}
        units = [u for u in units
                 if u.mechanism in needles
                 or u.mechanism.split(":", 1)[-1] in needles]
    if a.max_units and a.max_units < len(units):
        units = units[:a.max_units]
    print("findings", len(findings), "units", len(units))

    cfg = N.load_config(ROOT / "llm_config.yaml")
    decide_policy = None
    if a.llm_decide:
        from upi.agents.flow_policy import llm_decide
        decide_policy = lambda f, r, m: llm_decide(cfg, f, r, m)  # noqa: E731
    store = TicketStore()
    plans, bases = {}, {}
    unit_metric: dict[str, str] = {}
    unit_inv: dict[str, tuple[str, str, str, int]] = {}
    round_no = 1
    after_metrics: dict = {}
    after_start = 0.0

    def plan(unit):
        h = next((x for x in hs
                  if str(x.get("root_cause")) in unit.targets
                  or str(x.get("root_cause")) == unit.mechanism), None)
        if h is None and unit.targets:
            anchors = [s for s in unit.sites if "::" in s]
            h = {"root_cause": unit.targets[0], "code_anchors": anchors[:4]}
        if h is None:
            return None
        variants = []
        seen_plans = []
        meta = {}
        for _k in range(max(1, a.plan_repeat)):
            _pl, _meta = plan_issue(
                cfg, X._issue(d["issue"]), h, p,
                strategy=f"round{round_no}",
                required_sites=unit.sites,
                mechanism_class="unknown")
            seen_plans.append(_pl)
            variants.append({
                "ok": _pl is not None,
                "key": plan_key(_pl),
                "metric": (_pl.metric if _pl else ""),
                "invariant": (_pl.invariant if _pl else {}),
                "diffs": (_pl.diffs if _pl else []),
                "error": _meta.get("error", ""),
            })
            meta = _meta
        min_votes = a.plan_min_votes if a.plan_repeat > 1 else 1
        pl, stable, counts = choose_consensus(seen_plans, min_votes=min_votes)
        if pl is None:
            meta = {"error": "unstable_plan",
                    "why": f"重复 {len(seen_plans)} 次无多数方案",
                    "counts": counts}
        uniq = len({v["key"] for v in variants if v["ok"]})
        print("plan", unit.unit_id, unit.mechanism, bool(pl),
              meta.get("error", ""),
              f"repeat={len(variants)} unique={uniq} stable={stable}")
        try:
            rp = run_dir / "plan_repeat.json"
            old = {}
            if rp.exists():
                try:
                    old = json.loads(rp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    old = {}
            old[unit.unit_id] = {
                "variants": variants, "stable": stable,
                "selected": plan_key(pl) if pl else "",
                "min_votes": min_votes,
            }
            rp.write_text(json.dumps(old, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        except OSError:
            pass
        if pl is None:
            return None
        unit_metric[unit.unit_id] = pl.metric or ""
        inv = pl.invariant or {}
        if inv:
            unit_inv[unit.unit_id] = (
                "declared",
                str(inv.get("pattern") or ""),
                f"{inv.get('file','')}::{inv.get('method','')}",
                int(inv.get("expect", 0)))
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
        nonlocal after_metrics, after_start
        after_start = time.time()
        ok, _ = capture_with_recovery("batch-after", two_pass=False,
                                      light=True, fast=False,
                                      target_windows=True)
        if not ok:
            raise RuntimeError("batch capture failed")
        after_metrics = counters_since(after_start)
        cands, _, _ = candidates_from_latest(p, limit=12)
        return {str(c.root_cause).split(":", 1)[-1] for c in cands}

    def verify(unit, obs):
        # G21：回归形态 verified 至少需要 3 次基线；1 次只能算流程冒烟
        if a.baselines < 3:
            print("verify blocked: baselines<3 (smoke only)", a.baselines)
            return "needs_recheck"
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
        # I20：机制不变量优先，由补丁/源码模式推导，不查类名表
        inv = unit_inv.get(unit.unit_id)
        if inv:
            from upi.core.mechanism_checks import invariant_ok as _inv_ok
            name, pat, scope, expect = inv
            file, _, method = scope.partition("::")
            _ok, _detail = _inv_ok(p, file, method, pat, expect)
            print("invariant-verify", unit.unit_id, name, _ok, _detail,
                  "new=", new)
            if not _ok or new:
                return "partial"
            return "partial" if sa else "verified"
        from upi.core.verification_v2 import evaluate_v2
        # I20：metric delta + 机制不变量；缺失才退回 cluster
        if baseline_metrics and after_metrics:
            from upi.core.targets import evaluate as _eval_target
            _spec, _obs, _tv = _eval_target(
                {"finding_id": unit.unit_id, "mechanism": unit.mechanism},
                baseline_metrics, after_metrics,
                metric=unit_metric.get(unit.unit_id, "frame_time"),
                invariant_ok=True,
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

    def batch_fn(active_units, round_no):
        return run_batch(active_units, plan=plan, apply=apply,
                         compile_fn=compile_fn, capture=capture_fn,
                         verify=verify, rollback=rollback,
                         round_no=round_no)

    active = list(units)
    flow.enter("plan", active=[u.unit_id for u in active])
    all_verified: set[str] = set()
    for r in range(1, max(1, a.rounds) + 1):
        round_no = r
        if not active:
            break
        flow.enter("round", round_no=r,
                   active=[u.unit_id for u in active])
        res, decision = flow.run_round(
            active, r, a.rounds, batch_fn=batch_fn, policy=decide_policy)
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
        if decision != "retry":
            active = []
            break
        active = [u for u in units if u.unit_id in failed]
        if not active:
            break
    flow.enter("record", verified=sorted(all_verified), route="done")
    run.stop_reason = f"batch:verified={len(all_verified)}"
    save_run(run)
    paper = json.loads((ROOT / "runs" / "i8_lab" / "paper.json")
                       .read_text(encoding="utf-8"))
    print(json.dumps(score_run(paper, run), ensure_ascii=False, indent=1))
    print("elapsed", round(time.time() - t0, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
