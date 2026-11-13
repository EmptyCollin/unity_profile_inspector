"""I11d：受控 Agent loop（白名单、预算、确定性停止）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.agent_loop import run_loop  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 1) resident 调 replay → 拒绝，不执行
calls = []
r = run_loop("resident",
             decide=lambda s: {"tool": "replay_once"},
             act=lambda d: calls.append(1) or {"ok": True})
ok("resident 越权工具被拒", r.stop_reason == "tool_denied" and not calls,
   (r.stop_reason, calls))
ok("越权留痕", r.trace and "denied" in r.trace[0], r.trace)

# 2) 步数上限：resident 最多 1 步
r = run_loop("resident",
             decide=lambda s: {"tool": "capture_window"},
             act=lambda d: {"ok": True})
ok("resident 只走 1 步", r.steps == 1 and r.stop_reason == "max_steps",
   (r.steps, r.stop_reason))

# 3) 停止条件
r = run_loop("regression",
             decide=lambda s: {"tool": "capture_window"},
             act=lambda d: {"ok": True, "done": True},
             should_stop=lambda s: s["last"].get("done") is True)
ok("条件满足即停", r.stop_reason == "condition_met" and r.steps == 1, r.stop_reason)

# 4) token 预算
r = run_loop("regression",
             decide=lambda s: {"tool": "capture_window", "tokens": 9000},
             act=lambda d: {"ok": True})
ok("token 超预算即停", r.stop_reason == "max_steps" and r.tokens >= 12000,
   (r.stop_reason, r.tokens))

# 5) 工具异常 → 停并留痕
r = run_loop("resident", decide=lambda s: {"tool": "capture_window"},
             act=lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
ok("工具异常有界停止", r.stop_reason == "tool_error"
   and "boom" in r.trace[0]["observation"]["error"], r.trace)

# 6) 主动 stop
r = run_loop("regression", decide=lambda s: {"stop": True, "reason": "够了"},
             act=lambda d: {})
ok("主动 stop 生效", r.stop_reason == "够了" and r.steps == 0, r.stop_reason)

print(f"\n==== Agent loop 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
