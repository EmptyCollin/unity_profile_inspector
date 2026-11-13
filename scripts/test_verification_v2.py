"""I15 统一验证判定测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.verification_v2 import evaluate_v2  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


r = evaluate_v2(target_gone=True, new_clusters=[], guards_ok=True)
ok("干净+基线 → verified", r.status == "verified")
r = evaluate_v2(target_gone=True, new_clusters=[], guards_ok=True,
                sites_remaining={"X": ["A.cs"]})
ok("站点残留 → partial_fix", r.status == "partial_fix")
r = evaluate_v2(target_gone=True, new_clusters=[], guards_ok=True,
                series=[1000, 10, 10])
ok("1000→10 → pseudo_fix", r.status == "pseudo_fix" and r.pseudo, r.reason)
r = evaluate_v2(target_gone=True, new_clusters=[], guards_ok=True,
                series=[1000, 0, 0])
ok("1000→0 → verified", r.status == "verified")
r = evaluate_v2(target_gone=True, new_clusters=[], guards_ok=True,
                baselines_ok=False)
ok("基线不足 → observed", r.status == "observed")
r = evaluate_v2(target_gone=False, new_clusters=[], guards_ok=True)
ok("目标仍在 → needs_recheck", r.status == "needs_recheck")

print(f"\n==== I15 验证判定测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
