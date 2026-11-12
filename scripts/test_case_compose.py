"""多 case 组合测试：无冲突可叠加，冲突必须拒绝。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_cases  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim" / "project"
CASES = [ROOT / "sim" / "cases" / "c01", ROOT / "sim" / "cases" / "c02",
         ROOT / "sim" / "cases" / "c03"]
base_board = (PROJECT / "Assets/DevSim/Runtime/BoardModel.cs").read_bytes()
base_game = (PROJECT / "Assets/DevSim/Runtime/ChromaGame.cs").read_bytes()
out = Path(tempfile.mkdtemp(prefix="upi_combo_")) / "project"
try:
    run = apply_cases(PROJECT, CASES, out)
    ok("c01+c02+c03 组合", run.case_ids == ["c01", "c02", "c03"] and len(run.files) == 2, run)
    board = (out / "Assets/DevSim/Runtime/BoardModel.cs").read_text(encoding="utf-8")
    game = (out / "Assets/DevSim/Runtime/ChromaGame.cs").read_text(encoding="utf-8")
    ok("c01 病存在", "var hit = new bool[N, N];" in board and "_hitBuffer" not in board)
    ok("c02 病存在", "lastHudRound" not in game and 'roundText.text = "ROUND " + round;' in game)
    ok("c03 病存在", "lastItemCounts[i] != inventory[i]" not in game
       and "if (itemCountLabels[i] != null) itemCountLabels[i].text" in game)
    ok("base 不冲突",
       (PROJECT / "Assets/DevSim/Runtime/BoardModel.cs").read_bytes() == base_board
       and (PROJECT / "Assets/DevSim/Runtime/ChromaGame.cs").read_bytes() == base_game)

    # 人为制造同路径冲突
    bad = Path(tempfile.mkdtemp(prefix="upi_badcase_")) / "c03"
    shutil.copytree(ROOT / "sim" / "cases" / "c01", bad)
    m = json.loads((bad / "manifest.json").read_text(encoding="utf-8"))
    m["case_id"] = "c03"
    (bad / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    try:
        apply_cases(PROJECT, [ROOT / "sim" / "cases" / "c01", bad],
                    Path(tempfile.mkdtemp(prefix="upi_conflict_")) / "project")
        ok("同路径冲突拒绝", False)
    except ValueError:
        ok("同路径冲突拒绝", True)
finally:
    shutil.rmtree(out.parent, ignore_errors=True)

print(f"\n==== 多 case 组合测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
