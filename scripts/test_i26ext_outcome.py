"""I26-ext E0：outcome 表。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.outcome import compute, markdown  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


records = [
    {"status": "verified", "measurement_status": "measured"},
    {"status": "semantic_risk", "semantic_risk": "empty_replace"},
    {"status": "unmeasured"},
    {"status": "metric_mismatch"},
    {"status": "no_plan"},
]
d = compute(records, n_findings=6, elapsed_s=600)
ok("verified", d["verified"] == 1, d)
ok("measured", d["measured"] == 1, d)
ok("semantic", d["semantic_risk"] == 1, d)
ok("unmeasured", d["unmeasured"] == 1, d)
ok("metric_mismatch", d["metric_mismatch"] == 1, d)
ok("cost_per_verified", d["cost_per_verified_s"] == 600.0, d)
ok("markdown 有 cost", "cost_per_verified" in markdown(d))
d0 = compute([], n_findings=3, elapsed_s=10)
ok("无 verified 不除零", d0["cost_per_verified_s"] is None, d0)

print(f"\n==== I26-ext outcome 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
