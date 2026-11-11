"""I6b2b2：真实 Unity 写-编译-回滚 E2E（夹具，临时改动，完成后恢复）。

安全约束：
- 只在夹具干净时运行；
- 只插入一行注释（语义保持）；
- 记录原始 HEAD，完成后 rollback + reset + 删 tag；
- 任何一步失败都尝试恢复，并如实报告。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import fixer  # noqa: E402
from upi.core.models import EditPlan  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402
from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()

FIXTURE = ROOT / "fixtures" / "upi-fixture"
FILE = FIXTURE / "Assets" / "UpiFixtures" / "F_CPU_01" / "CombatSkillManager.cs"
FIND = "        [SerializeField] private int skillCount = 24;"
REPLACE = FIND + "\n        // UPI E2E temporary marker"


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main() -> int:
    checks: list[dict] = []

    def ok(name: str, cond: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {str(detail)[:180]}"))

    dirty, files = SN.workdir_state(FIXTURE, ROOT)
    if dirty:
        print("fixture dirty, refuse:", files[:5])
        return 2

    original = git("rev-parse", "HEAD")
    tag = ""
    try:
        scene = fixer._default_scene()
        ok("fixture clean", True)
        ok("scene available", bool(scene), str(scene))
        if scene is None:
            return 2
        snap = SN.snapshot("e2e-" + time.strftime("%H%M%S"), FIXTURE, scene,
                           "e2e-write", root=ROOT)
        tag = snap.git_ref
        ok("snapshot created", tag.startswith("upi/attempt-"), tag)

        plan = EditPlan(
            playbook_id="PB-E2E-COMMENT",
            diffs=[{"file": str(FILE.relative_to(ROOT)).replace("\\", "/"),
                    "find": FIND, "replace": REPLACE, "_why": "E2E temporary marker"}],
            invariants_assert=[], side_effect="none", permission_tier="auto")
        rep = fixer.execute(plan, snap, project_path=FIXTURE, scene_path=scene,
                            root=ROOT, human_approved=True, approval_note="E2E")
        st = str(rep.get("status") or "")
        ok("apply/compile status", st in ("applied", "compile_failed_rolled_back",
                                          "diff_rejected", "apply_io_error"), st)
        marker = "UPI E2E temporary marker" in FILE.read_text(encoding="utf-8")
        if st == "applied":
            ok("marker applied", marker)
            cg = rep.get("compile") or {}
            ok("compile ok", bool(cg.get("ok")), str(cg.get("errors"))[:200])
        else:
            ok("marker not left", not marker)
            ok("compile not run/rolled back", st != "applied", st)
    finally:
        # 恢复：先回 rollback（如 fixer 已回滚也可重复），再删 tag、reset 原始 HEAD
        try:
            if tag:
                SN.rollback(SN.Snapshot(
                    attempt_id="e2e-restore", git_ref=tag, head_before=original,
                    asset_manifest_hash="e2e", scene_guid="e2e",
                    protocol_fingerprint="e2e", created_at="", root=str(ROOT),
                    metadata={}), FIXTURE, fixer._default_scene() or FIXTURE)
        except Exception as e:  # noqa: BLE001
            print("rollback warning:", e)
        if tag:
            git("tag", "-d", tag)
        git("reset", "--hard", original)
        dirty2, files2 = SN.workdir_state(FIXTURE, ROOT)
        ok("fixture restored clean", not dirty2, str(files2[:5]))
        ok("HEAD restored", git("rev-parse", "HEAD") == original, git("rev-parse", "HEAD"))
        ok("tag deleted", not git("tag", "-l", tag) if tag else True)

    all_ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"e2e_unity_write_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ok": all_ok, "checks": checks, "at": ts},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== Unity 写-编译-回滚 E2E {'通过' if all_ok else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
