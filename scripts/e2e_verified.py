"""I4b2b2b2b2：缺陷态 3 基线 → 产品修复 → 修复后 replay → verified。"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import fixer  # noqa: E402
from upi.agents.nodes import _plan_for  # noqa: E402
from upi.config.project_profile import active_profile  # noqa: E402
from upi.core.cluster import cluster_events  # noqa: E402
from upi.core.regression import (BaselineMatrix, RegressionPlan,  # noqa: E402
                                 RegressionRunner, RegressionStore,
                                 ReplaySuite, merge_gap_for, verify_after)
from upi.core.replay_driver import UnityReplayDriver  # noqa: E402
from upi.tooling import probe_control as PC  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402


def git(*a):
    p = subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main() -> int:
    prof = active_profile()
    fixture = Path(prof.abs_project)
    target_file = prof.subject_file_abs
    ref = prof.defect_ref(prof.fix_subject)
    if target_file is None or not ref.exists():
        print("profile ref/target missing")
        return 2
    dirty, files = SN.workdir_state(fixture, ROOT)
    if dirty:
        print("fixture dirty:", files[:5])
        return 2

    original = git("rev-parse", "HEAD")
    original_bytes = target_file.read_bytes()
    snap = None
    checks = []
    result = {}

    def ok(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {str(detail)[:200]}"))

    try:
        target_file.write_bytes(ref.read_bytes())
        git("add", str(target_file.relative_to(ROOT)).replace("\\", "/"))
        git("commit", "-m", "TEMP: verified defect")
        ok("defect compiled", fixer.compile_gate(fixture, wait_s=120).get("ok"))
        ok("probe installed", PC.install().get("ok"))
        ok("probe alive", PC.wait_alive(15).get("state") == "alive")
        PC.send_cmd("gcthr:20000")

        suite = ReplaySuite(
            suite_id="e2e-verified", name="e2e verified",
            project=str(fixture),
            scene="Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity",
            protocol_fingerprint="fp-e2e-verified", warmup_s=2, window_s=2)
        store = RegressionStore(ROOT / "runs" / "_e2e_verified")
        store.save_suite(suite)
        drv = UnityReplayDriver(warmup_s=2, window_s=2)
        runner = RegressionRunner(suite, store, drv.run_once, align=True)
        m = runner.collect_baseline(3)
        ok("3 baselines", len(m.runs) == 3, str([r.event_count for r in m.runs]))
        plan = RegressionPlan.from_matrix(m)
        ok("plan ready", plan.status == "ready" and bool(plan.items), plan.status)
        if plan.status != "ready" or not plan.items:
            return 2
        target_id = plan.items[0].cluster_id
        target = next((c for r in m.runs for c in r.clusters
                       if c.cluster_id == target_id), None)
        ok("target breach cluster", target is not None and target.kind == "breach",
           target_id)

        snap = SN.snapshot("e2e-verified", fixture, fixer._default_scene(),
                           "e2e-verified", root=ROOT)
        plan_fix = _plan_for("per_frame_heap_alloc_in_hot_path")
        rep = fixer.execute(plan_fix, snap, project_path=fixture,
                            scene_path=fixer._default_scene(), root=ROOT,
                            human_approved=True, approval_note="E2E verified")
        ok("fix applied+compiled", rep.get("status") == "applied", rep.get("status"))
        ok("probe reinstalled", PC.install().get("ok"))
        ok("probe alive after fix", PC.wait_alive(15).get("state") == "alive")
        PC.send_cmd("gcthr:20000")

        proto, after_events = drv.run_once(suite)
        after_cs = cluster_events(after_events,
                                  merge_gap=merge_gap_for(suite),
                                  merge_sample=True)
        after_breach = [c for c in after_cs if c.kind == "breach"]
        before_guards = {}
        for k in {k for r in m.runs for k in r.guards}:
            vals = [r.guards[k] for r in m.runs if k in r.guards]
            before_guards[k] = statistics.median(vals)
        after_guards = {}
        from upi.core.regression import guards_from_events
        after_guards = guards_from_events(after_events)
        v = verify_after(m, target, after_breach, after_protocol=proto,
                         guards_before=before_guards, guards_after=after_guards)
        ok("verified verdict", v.status == "verified"
           and v.verification_status == "verified",
           f"{v.status}/{v.verification_status}: {v.reason}")
        result = {"verdict": v.model_dump(),
                  "target": target.summary(),
                  "after_breach_clusters": [c.summary() for c in after_breach],
                  "baseline_runs": [r.event_count for r in m.runs],
                  "guards_before": before_guards,
                  "guards_after": after_guards}
    finally:
        if snap is not None:
            try:
                SN.rollback(snap, fixture, fixer._default_scene() or fixture)
            except Exception as e:  # noqa: BLE001
                print("rollback warning:", e)
            git("tag", "-d", snap.git_ref)
        git("reset", "--hard", original)
        ok("original bytes restored", target_file.read_bytes() == original_bytes)
        ok("fixture clean", not SN.workdir_state(fixture, ROOT)[0])
        ok("HEAD restored", git("rev-parse", "HEAD") == original)

    all_ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"e2e_verified_{ts}.json"
    out.write_text(json.dumps({"ok": all_ok, "checks": checks, "result": result,
                               "at": ts}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n==== verified E2E {'通过' if all_ok else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
