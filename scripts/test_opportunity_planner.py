"""I7d：source-aware planner 只读当前源码；不依赖 _refs/profile-id。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.fixer import apply_diffs  # noqa: E402
from upi.core.opportunity import scan  # noqa: E402
from upi.core.opportunity_planner import plan_for  # noqa: E402

PASS, FAIL = [], []
PROJECT = ROOT / "sim" / "project"


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def main() -> int:
    flags = {"legacy_ui", "legacy_material", "legacy_match_alloc", "sync_shop_load"}
    findings = scan(PROJECT, project_id="chroma-rogue", active_flags=flags)
    by = {f.pattern_id: f for f in findings}
    ok("4 个 active 机会", len(findings) == 4, str(sorted(by)))

    ui = by.get("ui_text_rebuild_per_frame")
    plan = plan_for(ui, PROJECT) if ui else None
    ok("UI plan 生成", plan is not None and plan.permission_tier == "auto")
    ok("plan 不引用 _refs", plan is not None and "_refs" not in plan.model_dump_json())

    mat = by.get("per_instance_material")
    plan2 = plan_for(mat, PROJECT) if mat else None
    ok("材质 plan 为 semi", plan2 is not None and plan2.permission_tier == "semi",
       plan2.permission_tier if plan2 else None)

    sync = by.get("sync_resources_load_on_shop")
    ok("同步加载无通用模板→None", sync is not None and plan_for(sync, PROJECT) is None)

    # 在当前源码的临时副本上验证 find/replace，不碰真实工程
    if plan is not None:
        tmp = Path(tempfile.mkdtemp(prefix="upi_plan_"))
        try:
            rel = plan.diffs[0]["file"]
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / rel, dst)
            apply_diffs(plan, root=tmp)
            text = dst.read_text(encoding="utf-8")
            ok("diff 已应用", plan.diffs[0]["find"] not in text
               and plan.diffs[0]["replace"] in text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n==== 机会 planner 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
