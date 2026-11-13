"""I14：错误分类 / 有界重试 / checkpoint。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.resilience import (Checkpoint, call_with_retry,  # noqa: E402
                                 classify_error, policy_for)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("超时归临时", classify_error("timeout after 30s") == "transient")
ok("连接拒绝归临时", classify_error("connection refused") == "transient")
ok("空候选归数据", classify_error("empty candidates") == "data")
ok("权限归确定性", classify_error("permission denied") == "deterministic")
ok("无方案归逻辑", classify_error("no plan for hypothesis") == "logic")
ok("未知归 unknown", classify_error("weird thing") == "unknown")

ok("临时可重试 2 次", policy_for("transient").attempts == 2)
ok("确定性不重试", policy_for("deterministic").attempts == 0)

calls = []


def flaky():
    calls.append(1)
    if len(calls) < 3:
        raise TimeoutError("timeout")
    return "ok"


okres, val, kind, attempts = call_with_retry(flaky, stage="unit")
ok("临时错误重试后成功", okres and val == "ok" and attempts == 3,
   (okres, val, kind, attempts))

calls.clear()
okres2, val2, kind2, attempts2 = call_with_retry(
    lambda: (_ for _ in ()).throw(PermissionError("permission denied")),
    stage="unit")
ok("确定性错误不重试", not okres2 and kind2 == "deterministic"
   and attempts2 == 1, (kind2, attempts2))

TMP = Path(tempfile.mkdtemp())
cp = Checkpoint(TMP / "run")
cp.save(step="capture", status="done", data={"run": "r1"})
cp.save(step="diagnose", status="failed")
ok("checkpoint 记录", cp.done("capture") and not cp.done("diagnose"))
ok("resume 指向失败步", cp.resume_point(["capture", "diagnose", "plan"])
   == "diagnose", cp.load())
cp.save(step="diagnose", status="done")
ok("完成后 resume=plan", cp.resume_point(["capture", "diagnose", "plan"])
   == "plan")

print(f"\n==== 韧性层测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
