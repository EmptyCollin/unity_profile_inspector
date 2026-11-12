"""I7d：OpportunityFinding → BatchTicket（组合病例工程，源码配方）. """
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.core.opportunity import scan  # noqa: E402
from upi.core.ticket import BatchTicket, TicketStore, build_opportunity_ticket  # noqa: E402
from _compose import compose  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def main() -> int:
    project = compose([f"c{i:02d}" for i in range(1, 12)])
    fs = scan(project, project_id="paper")
    t = build_opportunity_ticket(fs, project, project_id="paper")
    ok("工单包含 9 个机会", len(t.items) == 9, len(t.items))
    by = {i.pattern_id: i for i in t.items}
    ok("UI 是 auto/pending 且有源码 plan",
       by["ui_text_rebuild_per_frame"].permission_tier == "auto"
       and by["ui_text_rebuild_per_frame"].status == "pending"
       and by["ui_text_rebuild_per_frame"].plan is not None)
    ok("材质无源码配方 → blocked/needs_human",
       by["per_instance_material"].permission_tier == "blocked"
       and by["per_instance_material"].status == "needs_human")
    ok("sync 无源码配方 → needs_human 且无 plan",
       by["sync_resources_load_on_shop"].plan is None
       and by["sync_resources_load_on_shop"].status == "needs_human")
    ok("工单 approval token 非空", len(t.approve_token) == 4, t.approve_token)

    tmp = Path(tempfile.mkdtemp(prefix="upi_ticket_"))
    store = TicketStore(tmp)
    store.save(t)
    loaded = store.load(t.ticket_id)
    ok("工单持久化/加载", isinstance(loaded, BatchTicket)
       and len(loaded.items) == 9 and loaded.project_id == "paper")

    print(f"\n==== 机会工单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
