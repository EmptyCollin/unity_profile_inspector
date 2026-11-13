"""I11g：模式不变量守卫（代码层拒绝 + 单一能力源）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mode_caps import (assert_tool_allowed,  # noqa: E402
                                assert_verdict_allowed, caps)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 1) verdict 守卫
try:
    assert_verdict_allowed("resident", "verified")
    ok("resident 写 verified 被拒", False)
except PermissionError:
    ok("resident 写 verified 被拒", True)
assert_verdict_allowed("regression", "verified")
ok("regression 可写 verified", True)

# 2) 工具守卫
for mode, tool in (("resident", "replay_once"), ("resident", "baseline_run"),
                   ("regression", "replay_once")):
    try:
        assert_tool_allowed(mode, tool)
        got = True
    except PermissionError:
        got = False
    want = mode == "regression"
    ok(f"{mode}/{tool} 允许={want}", got is want)

# 3) 单一能力源：关键模块必须走 mode_caps，而不是各自写死
src = lambda p: (ROOT / p).read_text(encoding="utf-8")
ok("verify_ladder 调用 verdict 守卫",
   "assert_verdict_allowed" in src("upi/core/verify_ladder.py"))
ok("agent_loop 调用工具守卫",
   "assert_tool_allowed" in src("upi/core/agent_loop.py"))
ok("unity_exec 读能力表", "mode_caps" in src("upi/core/unity_exec.py"))
ok("resident 能力表无 replay",
   not caps("resident").allow_replay and not caps("resident").allow_verified)

print(f"\n==== 模式守卫测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
