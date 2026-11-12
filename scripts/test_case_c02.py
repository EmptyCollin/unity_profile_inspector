"""c02 overlay 契约测试：HUD 缓存回退，base 不动。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_case  # noqa: E402
from upi.core.replay_spec import load_replay  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim" / "project"
CASE = ROOT / "sim" / "cases" / "c02"
base_before = (PROJECT / "Assets/DevSim/Runtime/ChromaGame.cs").read_bytes()
out = Path(tempfile.mkdtemp(prefix="upi_c02_")) / "project"
try:
    run = apply_case(PROJECT, CASE, out)
    ok("c02 应用", run.case_id == "c02" and run.files == ["Assets/DevSim/Runtime/ChromaGame.cs"], run)
    text = (out / "Assets/DevSim/Runtime/ChromaGame.cs").read_text(encoding="utf-8")
    ok("HUD 缓存已回退", "lastHudRound" not in text and 'roundText.text = "ROUND " + round;' in text)
    ok("item 缓存保留", "lastItemCounts" in text)
    ok("base 未被修改",
       (PROJECT / "Assets/DevSim/Runtime/ChromaGame.cs").read_bytes() == base_before)
    rep = load_replay(ROOT / "sim/replay/c02.json")
    ok("c02 replay 绑定 base", rep.replay_id == "c02" and
       rep.base_hash == json.loads((CASE / "manifest.json").read_text(encoding="utf-8"))["base_hash"])
    ok("c02 oracle", json.loads((ROOT / "sim/oracle/c02.json").read_text(encoding="utf-8"))["evaluator_only"] is True)
finally:
    shutil.rmtree(out.parent, ignore_errors=True)

print(f"\n==== c02 overlay 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
