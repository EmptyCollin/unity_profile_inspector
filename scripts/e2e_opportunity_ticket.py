"""I7d：机会工单 → 批准 → 执行（Unity）→ 回滚。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.opportunity import scan  # noqa: E402
from upi.core.ticket import (TicketStore, approve,  # noqa: E402
                             build_opportunity_ticket)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402

PROJECT = ROOT / "sim" / "project"
SCENE = PROJECT / "Assets" / "Scenes" / "00_Bootstrap.unity"


def git(*args):
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main() -> int:
    original = git("rev-parse", "HEAD")
    tags_before = set(git("tag", "-l", "upi/attempt-*").splitlines())
    flags = {"legacy_ui", "legacy_material", "legacy_match_alloc", "sync_shop_load"}
    findings = scan(PROJECT, project_id="chroma-rogue", active_flags=flags)
    store = TicketStore(Path(tempfile.mkdtemp(prefix="upi_opp_exec_")))
    ticket = build_opportunity_ticket(findings, PROJECT, project_id="chroma-rogue")
    store.save(ticket)
    checks = []

    def ok(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:300]})
        print(f"  [{'ok' if cond else 'FAIL'}] {name}"
              + ("" if cond else f" -> {str(detail)[:180]}"))

    try:
        good, msg = approve(ticket, approver="e2e", note="I7d opportunity E2E",
                            token=ticket.approve_token)
        ok("工单批准", good, msg)
        store.save(ticket)
        ex = UnityTicketExecutor(store, project=PROJECT, scene=SCENE)
        ticket = ex.execute(ticket)
        ok("自动项 applied_unverified",
           any(i.status == "applied_unverified" for i in ticket.items),
           [(i.pattern_id, i.status) for i in ticket.items])
        ok("半自动项 needs_human",
           any(i.permission_tier == "semi" and i.status == "needs_human"
               for i in ticket.items),
           [(i.pattern_id, i.permission_tier, i.status) for i in ticket.items])
        ok("sync semi 项 needs_human",
           any(i.pattern_id == "sync_resources_load_on_shop"
               and i.plan is not None and i.permission_tier == "semi"
               and i.status == "needs_human" for i in ticket.items))
        ok("工单 done", ticket.status == "done", ticket.status)
    finally:
        # 清掉本次 attempt tag + 回滚到原 HEAD
        for t in sorted(set(git("tag", "-l", "upi/attempt-*").splitlines()) - tags_before):
            git("tag", "-d", t)
        git("reset", "--hard", original)
        ok("工程已回滚", not git("status", "--short", "--", "sim/project") and git("rev-parse", "HEAD") == original)

    all_ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"opportunity_ticket_e2e_{ts}.json"
    out.write_text(json.dumps({"ok": all_ok, "checks": checks,
                               "ticket": ticket.model_dump()}, ensure_ascii=False,
                              indent=2), encoding="utf-8")
    print(f"\n==== 机会工单执行 E2E {'通过' if all_ok else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
