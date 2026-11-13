"""I11a：模式能力表（resident 不碰 replay/verified）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mode_caps import assert_tool_allowed, caps  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


r = caps("resident")
g = caps("regression")
ok("resident 无 replay 工具", not r.allow_replay
   and not r.allows("replay_once") and not r.allows("baseline_run"))
ok("regression 有 replay 工具", g.allow_replay and g.allows("replay_once"))
ok("resident 不许 verified", not r.allow_verified)
ok("regression 允许 verified", g.allow_verified)
ok("resident 证据源无 replay", "replay" not in r.evidence_providers)
ok("resident loop 更小", r.max_loop_steps < g.max_loop_steps)
ok("resident 编译重试更少", r.max_compile_retries <= g.max_compile_retries)

for mode, tool, should in (("resident", "replay_once", False),
                           ("regression", "replay_once", True),
                           ("resident", "capture_window", True)):
    try:
        assert_tool_allowed(mode, tool)
        got = True
    except PermissionError:
        got = False
    ok(f"{mode} 工具 {tool} 允许={should}", got is should)

print(f"\n==== 模式能力表测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
