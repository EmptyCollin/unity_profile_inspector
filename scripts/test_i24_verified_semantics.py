"""I24：verified 判定语义。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_verify import (decide, measurement_status,  # noqa: E402
                                    refine_partial)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


base = dict(baselines=3, invariant_ok=True, target_gone=True,
            new_clusters=[], sites_remaining=0, metric_status="verified")
ok("全部满足 → verified", decide(**base) == "verified")

ok("invariant 失败 → partial",
   decide(**{**base, "invariant_ok": False}) == "partial")
ok("target 未消失 → partial",
   decide(**{**base, "target_gone": False}) == "partial")
ok("mechanism 粒度可不要求 root 消失",
   decide(**{**base, "target_gone": False,
             "require_target_gone": False}) == "verified")
ok("有新 cluster → partial",
   decide(**{**base, "new_clusters": ["X"]}) == "partial")
ok("有剩余站点 → partial",
   decide(**{**base, "sites_remaining": 1}) == "partial")
ok("metric 不显著 → needs_recheck",
   decide(**{**base, "metric_status": "needs_recheck"}) == "needs_recheck")
ok("metric unmeasured → needs_recheck",
   decide(**{**base, "metric_status": "unmeasured"}) == "needs_recheck")
ok("基线不足 → needs_recheck",
   decide(**{**base, "baselines": 1}) == "needs_recheck")

ok("partial + 正改善 → improved_partial",
   refine_partial("partial", delta=100) == "improved_partial")
ok("needs_recheck + 正改善 → improved_partial",
   refine_partial("needs_recheck", delta=100) == "improved_partial")
ok("partial + 新 cluster 不保留",
   refine_partial("partial", delta=100, new_clusters=["X"]) == "partial")
ok("partial + 无改善不保留",
   refine_partial("partial", delta=0) == "partial")

ok("无 window → unmeasured",
   measurement_status(window="", after_n=3, before_n=3) == "unmeasured")
ok("after_n<3 → unstable",
   measurement_status(window="open_shop", after_n=1, before_n=3) == "unstable")
ok("before_n<3 → unmeasured",
   measurement_status(window="open_shop", after_n=3, before_n=2) == "unmeasured")
ok("充分 → measured",
   measurement_status(window="open_shop", after_n=3, before_n=3) == "measured")

print(f"\n==== I24 verified 语义测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
