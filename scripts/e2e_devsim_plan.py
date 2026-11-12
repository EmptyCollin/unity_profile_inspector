"""I7d：机会 → source-aware EditPlan → 真实 apply/compile/rollback（devsim）。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import fixer  # noqa: E402
from upi.core.opportunity import scan  # noqa: E402
from upi.core.opportunity_planner import plan_for  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402

PROJECT = ROOT / "sim" / "project"
SCENE = "Assets/Scenes/00_Bootstrap.unity"
SCENE_ABS = PROJECT / SCENE


def git(*args):
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main() -> int:
    flags = {"legacy_ui", "legacy_material", "legacy_match_alloc", "sync_shop_load"}
    findings = scan(PROJECT, project_id="chroma-rogue", active_flags=flags)
    by = {f.pattern_id: f for f in findings}
    checks = []
    results = []
    snap = None
    original = git("rev-parse", "HEAD")

    def ok(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:300]})
        print(f"  [{'ok' if cond else 'FAIL'}] {name}"
              + ("" if cond else f" -> {str(detail)[:160]}"))

    try:
        # UI 机会：auto，不需要人工批准
        ui = by.get("ui_text_rebuild_per_frame")
        plan = plan_for(ui, PROJECT) if ui else None
        ok("UI plan 存在", plan is not None)
        if plan is not None:
            snap = SN.snapshot("i7d-ui", PROJECT, SCENE_ABS, "devsim-i7d", root=ROOT)
            rep = fixer.execute(plan, snap, project_path=PROJECT,
                                scene_path=SCENE_ABS, root=PROJECT)
            ok("UI apply+compile", rep.get("status") == "applied", rep)
            results.append({"pattern": "ui_text_rebuild_per_frame",
                            "plan": plan.model_dump(), "result": rep})
            SN.rollback(snap, PROJECT, SCENE_ABS)
            git("tag", "-d", snap.git_ref)
            snap = None

        # 材质机会：semi，必须人工批准
        mat = by.get("per_instance_material")
        p2 = plan_for(mat, PROJECT) if mat else None
        ok("材质 plan 存在", p2 is not None)
        if p2 is not None:
            snap = SN.snapshot("i7d-mat", PROJECT, SCENE_ABS, "devsim-i7d", root=ROOT)
            rep2 = fixer.execute(p2, snap, project_path=PROJECT,
                                 scene_path=SCENE_ABS, root=PROJECT,
                                 human_approved=True,
                                 approval_note="I7d devsim E2E")
            ok("材质 apply+compile", rep2.get("status") == "applied", rep2)
            results.append({"pattern": "per_instance_material",
                            "plan": p2.model_dump(), "result": rep2})
            SN.rollback(snap, PROJECT, SCENE_ABS)
            git("tag", "-d", snap.git_ref)
            snap = None

        # sync Addressables 目前无通用模板，必须转人工
        sync = by.get("sync_resources_load_on_shop")
        ok("sync 无模板（转人工）", sync is not None and plan_for(sync, PROJECT) is None)

        ok("工程恢复干净", not SN.workdir_state(PROJECT, ROOT)[0])
    finally:
        if snap is not None:
            try:
                SN.rollback(snap, PROJECT, SCENE_ABS)
            except Exception as e:  # noqa: BLE001
                print("rollback warning:", e)
        if snap is not None:
            try:
                git("tag", "-d", snap.git_ref)
            except Exception:  # noqa: BLE001
                pass
        git("reset", "--hard", original)

    all_ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"devsim_plan_e2e_{ts}.json"
    out.write_text(json.dumps({"ok": all_ok, "checks": checks, "results": results,
                               "at": ts}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n==== devsim planner E2E {'通过' if all_ok else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
