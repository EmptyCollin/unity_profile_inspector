"""I8d：item-label cache recipe 的离线测试。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.agents.fixer import apply_diffs  # noqa: E402
from upi.core.repair_recipe import (plan_board_buffer_reuse,  # noqa: E402
                                    plan_for_candidate, plan_hud_text_cache,
                                    plan_item_label_cache)
from upi.core.source_index import SourceIndex  # noqa: E402
from _compose import compose  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


# 配方要的是“当前源码里真的有缺陷模板”：用组合病例工程，不用干净 base
PROJECT = compose(["c01", "c02", "c03"], prefix="upi_recipe_proj_")
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

hud = plan_hud_text_cache(PROJECT, idx)
ok("HUD recipe 生成", hud is not None)
if hud is not None:
    ok("HUD 五片 diff", len(hud.diffs) == 5, len(hud.diffs))
    tmp2 = Path(tempfile.mkdtemp(prefix="upi_recipe_hud_"))
    try:
        dst2 = tmp2 / "Assets/DevSim/Runtime/ChromaGame.cs"
        dst2.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / "Assets/DevSim/Runtime/ChromaGame.cs", dst2)
        apply_diffs(hud, root=tmp2)
        t2 = dst2.read_text(encoding="utf-8")
        ok("HUD 缓存字段", "lastHudRound" in t2 and "lastHudCoins" in t2)
        ok("HUD 条件更新", 'if (round != lastHudRound)' in t2)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

board = plan_board_buffer_reuse(PROJECT, idx)
ok("Board recipe 生成", board is not None)
if board is not None:
    ok("Board 三片 diff", len(board.diffs) == 3, len(board.diffs))
    tmp3 = Path(tempfile.mkdtemp(prefix="upi_recipe_board_"))
    try:
        dst3 = tmp3 / "Assets/DevSim/Runtime/BoardModel.cs"
        dst3.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT / "Assets/DevSim/Runtime/BoardModel.cs", dst3)
        apply_diffs(board, root=tmp3)
        t3 = dst3.read_text(encoding="utf-8")
        ok("Board 复用字段", "_hitBuffer" in t3 and "_perColorBuffer" in t3)
        ok("Board 清零", "Array.Clear(_hitBuffer" in t3)
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

class FakeCand:
    root_cause = "hot_function:ChromaGame.UpdateHud"
    sources = [type("S", (), {"symbol": "ChromaGame.UpdateHud"})()]

ok("候选→HUD recipe", plan_for_candidate(FakeCand(), PROJECT, idx) is not None)
FakeCand.root_cause = "hot_function:ChromaGame.RefreshItemLabels"
FakeCand.sources = [type("S", (), {"symbol": "ChromaGame.RefreshItemLabels"})()]
ok("候选→item recipe", plan_for_candidate(FakeCand(), PROJECT, idx) is not None)
FakeCand.root_cause = "hot_function:BoardModel.FindMatches"
FakeCand.sources = [type("S", (), {"symbol": "BoardModel.FindMatches"})()]
ok("候选→Board recipe", plan_for_candidate(FakeCand(), PROJECT, idx) is not None)

print(f"\n==== 修复 recipe 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
