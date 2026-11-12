"""I7d：source-aware planner 只读当前源码；不依赖 _refs/profile-id。

新病灶：UI / match 走源码配方；material / sync 没有源码配方时诚实返回 None。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.agents.fixer import apply_diffs  # noqa: E402
from upi.core.opportunity import scan  # noqa: E402
from upi.core.opportunity_planner import plan_for  # noqa: E402
from _compose import compose  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def main() -> int:
    project = compose([f"c{i:02d}" for i in range(1, 12)])
    findings = scan(project, project_id="paper")
    by = {f.pattern_id: f for f in findings}
    ok("扫描到 9 个机制", len(findings) >= 9, str(sorted(by)))

    ui = by.get("ui_text_rebuild_per_frame")
    plan = plan_for(ui, project) if ui else None
    ok("UI plan 生成（源码配方）",
       plan is not None and plan.permission_tier == "auto")
    ok("plan 不引用 _refs", plan is not None and "_refs" not in plan.model_dump_json())

    mat = by.get("per_instance_material")
    plan2 = plan_for(mat, project) if mat else None
    ok("材质无源码配方 → 诚实拒绝", plan2 is None)

    sync = by.get("sync_resources_load_on_shop")
    ps = plan_for(sync, project) if sync else None
    ok("sync 无源码配方 → 诚实拒绝", ps is None)

    for p, label in ((plan, "UI"),):
        if p is None:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="upi_plan_"))
        try:
            rel = p.diffs[0]["file"]
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / rel, dst)
            apply_diffs(p, root=tmp)
            text = dst.read_text(encoding="utf-8")
            ok(f"{label} diff 已应用", "lastHudRound" in text
               and len(p.diffs) == 5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n==== 机会 planner 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
