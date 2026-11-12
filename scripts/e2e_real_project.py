"""I7f-a：把 devsim 当真实工程跑完整验收闭环。

选工程（ProjectRegistry）→ 基线/机会 → 建单/批准 → apply/compile
→ smoke replay → 回滚 → 报告。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config.project_profile import load_profile, reset_profile  # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.opportunity import scan  # noqa: E402
from upi.core.ticket import (TicketStore, approve,  # noqa: E402
                             build_opportunity_ticket)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402

PROJECT = ROOT / "sim" / "project"
SCENE = PROJECT / "Assets" / "Scenes" / "00_Bootstrap.unity"
OUT = ROOT / "runs"
TMP = Path(tempfile.mkdtemp(prefix="upi_real_project_"))
E.isolate(TMP / "runs")
reset_profile()

checks: list[dict] = []
report = {"mode": "regression", "verification_status": "unverified", "checks": checks}


def ok(name: str, cond: bool, detail="") -> None:
    checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:500]})
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f" -> {str(detail)[:220]}"))


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def project_dirty() -> list[str]:
    out = git("status", "--porcelain", "--", "sim/project")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def run_smoke() -> dict:
    out = OUT / "real_project_smoke.png"
    p = subprocess.run(
        [sys.executable, str(ROOT / "sim" / "replay" / "devsim_replay.py"),
         "--case", "c18", "--wait", "2", "--out", str(out)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    return {"returncode": p.returncode, "stdout": (p.stdout or "")[-500:],
            "stderr": (p.stderr or "")[-500:], "screenshot": str(out),
            "screenshot_exists": out.exists()}


def main() -> int:
    original = git("rev-parse", "HEAD")
    tags_before = set(git("tag", "-l", "upi/attempt-*").splitlines())
    report.update({"project_path": str(PROJECT), "scene": str(SCENE),
                   "head_before": original})

    # 1. 用 UI 同一个 registry 选择真实工程
    reg = ProjectRegistry(E.PROJECTS)
    entry = reg.add(project_path=PROJECT, display_name="Chroma Rogue (devsim)",
                    default_scene="Assets/Scenes/00_Bootstrap.unity",
                    replay_mode="command",
                    replay_command="python sim/replay/devsim_replay.py --case {case}")
    reg.activate(entry.project_id)
    prof = load_profile(entry.project_id)
    ok("工程注册/激活", reg.active_id() == entry.project_id)
    ok("活动工程路径", prof.abs_project.resolve() == PROJECT.resolve(),
       prof.abs_project)
    ok("默认场景映射", prof.scene_of(prof.fix_subject).resolve() == SCENE.resolve(),
       prof.fix_subject)
    report["project_id"] = entry.project_id

    # 2. 基线：写运行期 feature 状态，跑当前源码的机会扫描
    feature_file = Path(tempfile.gettempdir()) / "devsim_active_features.json"
    flags = ["legacy_ui", "legacy_material", "legacy_match_alloc", "sync_shop_load"]
    feature_file.write_text(json.dumps({
        "project_path": str(PROJECT), "case_id": "c18",
        **{k: True for k in flags},
        "legacy_particle": False, "legacy_canvas": False,
        "legacy_texture": False, "legacy_hint": False, "legacy_anim": False,
    }), encoding="utf-8")
    findings = scan(PROJECT, project_id=entry.project_id, active_flags=set(flags))
    ok("基线扫描到 4 个机会", len(findings) == 4,
       [(f.pattern_id, f.metric) for f in findings])
    report["findings"] = [{"pattern_id": f.pattern_id, "metric": f.metric,
                           "code_anchor": str(f.code_anchor)} for f in findings]

    # 3. 建单/批准
    store = TicketStore(TMP / "tickets")
    ticket = build_opportunity_ticket(findings, PROJECT, project_id=entry.project_id)
    store.save(ticket)
    good, msg = approve(ticket, approver="i7f-a", note="devsim real-project E2E",
                        token=ticket.approve_token)
    ok("工单批准", good, msg)
    report["ticket_id"] = ticket.ticket_id

    # 4. apply/compile + keep；semi 应转人工
    before_dirty = project_dirty()
    ok("执行前工程干净", not before_dirty, before_dirty[:3])
    ex = UnityTicketExecutor(store, project=PROJECT, scene=SCENE)
    ticket = ex.execute(ticket)
    auto = [i for i in ticket.items if i.permission_tier == "auto"]
    semi = [i for i in ticket.items if i.permission_tier != "auto"]
    ok("auto 项已 apply+compile", auto and all(
        i.status == "applied_unverified" for i in auto),
       [(i.pattern_id, i.status) for i in auto])
    ok("semi 项转人工", semi and all(i.status == "needs_human" for i in semi),
       [(i.pattern_id, i.permission_tier, i.status) for i in semi])
    ok("工单 done", ticket.status == "done", ticket.status)
    report["items"] = [{"pattern_id": i.pattern_id, "tier": i.permission_tier,
                        "status": i.status, "error": i.error} for i in ticket.items]

    # 5. 回归 smoke：修后仍能启动 c18 并出画面
    smoke = run_smoke()
    ok("修后 smoke replay", smoke["returncode"] == 0 and smoke["screenshot_exists"],
       smoke)
    report["smoke"] = smoke
    report["verification_status"] = "observed" if smoke["returncode"] == 0 else "failed"

    # 6. 回滚到原 HEAD，清掉本次 attempt tag，确认工作区干净
    git("reset", "--hard", original)
    for tag in sorted(set(git("tag", "-l", "upi/attempt-*").splitlines()) - tags_before):
        git("tag", "-d", tag)
    after_dirty = project_dirty()
    ok("工程已回滚且干净", not after_dirty, after_dirty[:3])
    ok("HEAD 恢复", git("rev-parse", "HEAD") == original)
    report["head_after"] = git("rev-parse", "HEAD")
    report["clean_after"] = not after_dirty

    report["ok"] = all(c["ok"] for c in checks)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = OUT / f"real_project_e2e_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== I7f-a 真实工程验收: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
