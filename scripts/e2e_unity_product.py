"""I6b2b2b：真实产品 plan E2E（缺陷态夹具，临时提交，完成后恢复）。

流程：
1. 夹具干净时，把缺陷参照装回夹具文件并临时提交（让 snapshot 能建）；
2. snapshot；执行真实 `_plan_for` 产品 plan（blocked + human_approved）；
3. 编译；断言缺陷签名消失；
4. rollback + 删 tag + reset 原始 HEAD；
5. 断言原始文件逐字节恢复、工作区干净。
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
from upi.agents.nodes import _plan_for  # noqa: E402
from upi.config.project_profile import active_profile  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402
from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main() -> int:
    checks: list[dict] = []

    def ok(name: str, cond: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:500]})
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {str(detail)[:200]}"))

    prof = active_profile()
    fixture = Path(prof.abs_project)
    target = prof.subject_file_abs
    ref = prof.defect_ref(prof.fix_subject)
    if target is None or not ref.exists():
        print("profile missing target/ref")
        return 2
    dirty, files = SN.workdir_state(fixture, ROOT)
    if dirty:
        print("fixture dirty, refuse:", files[:5])
        return 2

    original_head = git("rev-parse", "HEAD")
    original_bytes = target.read_bytes()
    tag = ""
    try:
        ok("fixture clean", True)
        ok("defect ref has signature", prof.defect_signature in ref.read_text(encoding="utf-8"),
           prof.defect_signature)
        target.write_bytes(ref.read_bytes())
        git("add", str(target.relative_to(ROOT)).replace("\\", "/"))
        git("commit", "-m", "TEMP: defect state for product E2E")
        ok("temp defect committed",
           git("status", "--porcelain", "--",
               str(target.relative_to(ROOT)).replace("\\", "/")) == "")
        ok("fixture now defect", prof.defect_signature in target.read_text(encoding="utf-8"))

        scene = fixer._default_scene()
        snap = SN.snapshot("e2e-product-" + time.strftime("%H%M%S"), fixture, scene,
                           "e2e-product", root=ROOT)
        tag = snap.git_ref
        ok("snapshot created", tag.startswith("upi/attempt-"), tag)

        plan = _plan_for("per_frame_heap_alloc_in_hot_path")
        ok("product plan available", plan is not None)
        ok("plan is blocked", plan is not None and plan.permission_tier == "blocked",
           plan.permission_tier if plan else "")
        if plan is None:
            return 2
        for d in plan.diffs:
            fp = ROOT / d["file"]
            ok(f"find present: {Path(d['file']).name}", d["find"] in fp.read_text(encoding="utf-8"))
        rep = fixer.execute(plan, snap, project_path=fixture, scene_path=scene,
                            root=ROOT, human_approved=True, approval_note="E2E product plan")
        st = str(rep.get("status") or "")
        ok("product plan status", st == "applied", st)
        if st == "applied":
            body = target.read_text(encoding="utf-8")
            ok("defect signature gone", prof.defect_signature not in body)
            ok("fix marker present", "预生成" in body or "_body" in body)
            cg = rep.get("compile") or {}
            ok("compile ok", bool(cg.get("ok")), str(cg.get("errors"))[:200])
    finally:
        try:
            if tag:
                SN.rollback(SN.Snapshot(
                    attempt_id="e2e-product-restore", git_ref=tag,
                    head_before=original_head, asset_manifest_hash="e2e",
                    scene_guid="e2e", protocol_fingerprint="e2e",
                    created_at="", root=str(ROOT), metadata={}),
                    fixture, fixer._default_scene() or fixture)
        except Exception as e:  # noqa: BLE001
            print("rollback warning:", e)
        if tag:
            git("tag", "-d", tag)
        git("reset", "--hard", original_head)
        ok("original bytes restored", target.read_bytes() == original_bytes)
        dirty2, files2 = SN.workdir_state(fixture, ROOT)
        ok("fixture restored clean", not dirty2, str(files2[:5]))
        ok("HEAD restored", git("rev-parse", "HEAD") == original_head, git("rev-parse", "HEAD"))
        ok("tag deleted", not git("tag", "-l", tag) if tag else True)

    all_ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"e2e_unity_product_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ok": all_ok, "checks": checks, "at": ts},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== 产品 plan E2E {'通过' if all_ok else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
