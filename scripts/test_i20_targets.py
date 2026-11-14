"""I20 findings+counter → target verdict 测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.targets import evaluate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


before = {"counters": {"CPU Main Thread Frame Time":
                       {"series": [50, 51, 49, 50]}}}
after = {"counters": {"CPU Main Thread Frame Time":
                      {"series": [30, 31, 29, 30]}}}
f = {"finding_id": "f1", "mechanism": "canvas_layout_churn",
     "invariant": "ForceUpdateCanvases==0"}
spec, obs, v = evaluate(f, before, after, invariant_ok=True)
ok("metric 降 → verified", v.status == "verified", v)

spec, obs, v = evaluate(f, before, after, invariant_ok=False, pseudo=True)
ok("不变量失败 → pseudo", v.status == "pseudo", v)

spec, obs, v = evaluate(f, before, after, invariant_ok=True,
                        sites_remaining=2)
ok("站点剩余 → partial", v.status == "partial", v)

print(f"\n==== I20 targets 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
