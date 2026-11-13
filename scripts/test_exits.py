"""I13d：出口契约（状态集合、落盘、resident 守卫）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.exits import (EXIT_CODES, assert_exit_allowed,  # noqa: E402
                            is_terminal, record_exit)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("出口集合包含关键状态", {"insufficient_evidence", "not_reproduced",
                    "observed", "verified", "no_plan"} <= EXIT_CODES)
ok("证据不足是终态", is_terminal("insufficient_evidence"))
ok("未复现是终态", is_terminal("not_reproduced"))
ok("observed 不是图终态（可继续记录）", not is_terminal("observed"))

try:
    assert_exit_allowed("resident", "verified")
    ok("resident 禁止 verified", False)
except PermissionError:
    ok("resident 禁止 verified", True)
assert_exit_allowed("regression", "verified")
ok("regression 允许 verified", True)

TMP = Path(tempfile.mkdtemp())
p = record_exit(TMP / "run1", stage="detect", code="insufficient_evidence",
                reason="没有候选", mode="resident",
                details={"candidates": 0})
ok("出口落盘", p["code"] == "insufficient_evidence"
   and (TMP / "run1" / "exit.json").exists(), p)
back = json.loads((TMP / "run1" / "exit.json").read_text(encoding="utf-8"))
ok("出口含原因/模式/上下文", back["reason"] == "没有候选"
   and back["mode"] == "resident" and back["details"]["candidates"] == 0)

try:
    record_exit(TMP / "run2", stage="verify", code="verified",
                reason="x", mode="resident")
    ok("落盘也守 resident", False)
except PermissionError:
    ok("落盘也守 resident", True)

print(f"\n==== 出口契约测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
