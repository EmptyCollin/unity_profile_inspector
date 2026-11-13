"""I12d：重试策略（换假设、停滞、预算）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.retry_policy import (is_stagnant, max_attempts,  # noqa: E402
                                   next_untried)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


hs = [{"root_cause": "a"}, {"root_cause": "b"}, {"root_cause": "c"}]
ok("常驻只 1 次", max_attempts("resident") == 1)
ok("回归 > 1 且有封顶", 1 < max_attempts("regression") <= 3,
   max_attempts("regression"))
ok("首次选第一个", next_untried(hs, set())["root_cause"] == "a")
ok("已试 a 后选 b", next_untried(hs, {"a"})["root_cause"] == "b")
ok("都试过返回 None", next_untried(hs, {"a", "b", "c"}) is None)
ok("同 diff 判停滞", is_stagnant("h1", {"h1"}) is True)
ok("新 diff 不停", is_stagnant("h2", {"h1"}) is False)

print(f"\n==== 重试策略测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
