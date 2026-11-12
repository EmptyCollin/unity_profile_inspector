"""c01 replay/oracle 协议一致性测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import tree_hash  # noqa: E402
from upi.core.replay_spec import (ReplayCommand, load_replay,  # noqa: E402
                                   merge_replays)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim/project"
CASE = ROOT / "sim/cases/c01"
replay = load_replay(ROOT / "sim/replay/c01.json")
manifest = json.loads((CASE / "manifest.json").read_text(encoding="utf-8"))
ok("replay 载入", replay.replay_id == "c01" and len(replay.commands) == 8, replay)
ok("replay 绑定 base", replay.base_hash == tree_hash(PROJECT) == manifest["base_hash"],
   (replay.base_hash, tree_hash(PROJECT), manifest["base_hash"]))
ok("命令序列含起停/棋盘", replay.commands[0].type == "start_game"
   and replay.commands[-1].type == "stop"
   and any(c.type == "set_board" for c in replay.commands)
   and sum(c.type == "resolve" for c in replay.commands) == 2)
ok("oracle 文件存在且 evaluator_only",
   json.loads((ROOT / "sim/oracle/c01.json").read_text(encoding="utf-8"))["evaluator_only"] is True)
try:
    ReplayCommand(type="spawn")
    ok("缺 count 拒绝", False)
except ValueError:
    ok("缺 count 拒绝", True)
try:
    ReplayCommand(type="wait", seconds=-1)
    ok("负时间拒绝", False)
except ValueError:
    ok("负时间拒绝", True)

merged = merge_replays([
    load_replay(ROOT / "sim/replay/c01.json"),
    load_replay(ROOT / "sim/replay/c02.json"),
    load_replay(ROOT / "sim/replay/c03.json"),
])
ok("组合 replay 只有一个起停",
   merged.commands[0].type == "start_game" and merged.commands[-1].type == "stop"
   and sum(c.type == "start_game" for c in merged.commands) == 1
   and sum(c.type == "stop" for c in merged.commands) == 1)
ok("组合 replay 保留各 case 命令",
   sum(c.type == "set_board" for c in merged.commands) == 1
   and sum(c.type == "resolve" for c in merged.commands) == 2
   and sum(c.type == "wait" for c in merged.commands) == 5)

print(f"\n==== c01 replay/oracle 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
