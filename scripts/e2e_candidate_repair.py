"""I8b→I8d：从候选列表自动选 recipe，执行 apply/compile/selftest/rollback。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import fixer  # noqa: E402
from upi.core.candidates_v2 import CandidateV2  # noqa: E402
from upi.core.repair_recipe import plan_for_candidate  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402

PROJECT = ROOT / "sim" / "project"
SCENE = PROJECT / "Assets" / "Scenes" / "00_Bootstrap.unity"
checks = []


def ok(name, cond, detail=""):
    checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:500]})
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f" -> {str(detail)[:200]}"))


def git(*args):
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    a = ap.parse_args()
    original = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    ok("工作区干净", not dirty, dirty)
    if dirty:
        return 1
    opts = set(git("tag", "-l", "upi/attempt-*").splitlines())
    data = json.loads(Path(a.candidates).read_text(encoding="utf-8"))
    cands = [CandidateV2(**c) for c in data.get("candidates", [])]
    idx = SourceIndex.build(PROJECT)
    chosen = None
    plan = None
    for c in cands:
        p = plan_for_candidate(c, PROJECT, idx)
        if p is not None:
            chosen, plan = c, p
            break
    ok("从候选自动选到 recipe", plan is not None,
       [(c.kind, c.root_cause) for c in cands[:6]])
    if plan is None:
        return 1
    print(f"  chosen: {chosen.kind} {chosen.root_cause} -> {plan.playbook_id}")
    snap = None
    try:
        snap = SN.snapshot("i8d-candidate", PROJECT, SCENE, "i8d-auto", root=ROOT)
        rep = fixer.execute(plan, snap, project_path=PROJECT, scene_path=SCENE,
                            human_approved=True, approval_note="I8d auto recipe")
        ok("apply+compile", rep.get("status") == "applied", rep)
        ok("编译成功", (rep.get("compile") or {}).get("ok") is True, rep.get("compile"))
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "test_devsim_game.py")],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        ok("修后玩法自测", p.returncode == 0, (p.stdout or "")[-800:])
    finally:
        if snap is not None:
            rb = SN.rollback(snap, PROJECT, SCENE)
            ok("回滚", bool(rb.get("asset_manifest_restored")), rb)
        git("reset", "--hard", original)
        for tag in sorted(set(git("tag", "-l", "upi/attempt-*").splitlines()) - opts):
            git("tag", "-d", tag)
        ok("HEAD 恢复", git("rev-parse", "HEAD") == original)
        ok("工作区干净", not git("status", "--porcelain", "--", "sim/project"))
    report = {"ok": all(c["ok"] for c in checks), "checks": checks,
              "candidate": chosen.model_dump(mode="json"),
              "playbook": plan.playbook_id, "head": original}
    out = ROOT / "runs" / f"i8d_auto_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== 候选→recipe 自动执行: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
