"""I11e：验证阶梯（常驻不 verified；回归单次干净才跑基线）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.verify_ladder import evaluate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


r = evaluate("resident", replay_once=lambda: {"target_gone": True},
             baselines=lambda: True)
ok("常驻永不 verified", r.status == "unverified", r.status)

r = evaluate("regression", replay_once=lambda: {"target_gone": False})
ok("目标仍在 → needs_recheck", r.status == "needs_recheck", r.status)

r = evaluate("regression", replay_once=lambda: {"not_reproduced": True})
ok("未复现 → not_reproduced", r.status == "not_reproduced", r.status)

r = evaluate("regression", replay_once=lambda: {"target_gone": True,
                                                "new_clusters": ["new-a"]})
ok("出现新 cluster → needs_recheck", r.status == "needs_recheck"
   and r.new_clusters == ["new-a"], r.new_clusters)

r = evaluate("regression", replay_once=lambda: {"target_gone": True,
                                                "protocol_ok": False})
ok("协议不一致 → unverified 不对比", r.status == "unverified", r.reason)

r = evaluate("regression", replay_once=lambda: {"target_gone": True,
                                                "guards_ok": True})
ok("单次干净但无基线 → observed", r.status == "observed", r.reason)

r = evaluate("regression", replay_once=lambda: {"target_gone": True},
             baselines=lambda: True)
ok("单次干净 + 基线确认 → verified", r.status == "verified", r.reason)

r = evaluate("regression", replay_once=lambda: {"target_gone": True},
             baselines=lambda: False)
ok("基线不确认 → observed", r.status == "observed", r.status)

r = evaluate("regression", replay_once=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
ok("replay 异常 → unverified", r.status == "unverified" and "boom" in r.reason,
   r.reason)

print(f"\n==== 验证阶梯测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
