"""I8d：item-label cache recipe 的离线测试。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.fixer import apply_diffs  # noqa: E402
from upi.core.repair_recipe import plan_item_label_cache  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim" / "project"
idx = SourceIndex.build(PROJECT)
plan = plan_item_label_cache(PROJECT, idx)
ok("recipe 生成 plan", plan is not None)
if plan is None:
    print(f"\n==== 修复 recipe 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    raise SystemExit(1)
ok("只改一个文件", plan.touched_assets == ["Assets/DevSim/Runtime/ChromaGame.cs"],
   plan.touched_assets)
ok("两片 diff", len(plan.diffs) == 2, plan.diffs)
ok("auto 且语义保持", plan.permission_tier == "auto" and plan.side_effect == "none")

tmp = Path(tempfile.mkdtemp(prefix="upi_recipe_"))
try:
    src = PROJECT / "Assets/DevSim/Runtime/ChromaGame.cs"
    dst = tmp / "Assets/DevSim/Runtime/ChromaGame.cs"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    applied = apply_diffs(plan, root=tmp)
    text = dst.read_text(encoding="utf-8")
    ok("补丁已应用", len(applied) == 2, applied)
    ok("字段存在", "private int[] lastItemCounts;" in text)
    ok("旧无条件赋值消失",
       'if (itemCountLabels[i] != null) itemCountLabels[i].text' not in text)
    ok("变化时仍更新", "lastItemCounts[i] = inventory[i];" in text)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n==== 修复 recipe 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
