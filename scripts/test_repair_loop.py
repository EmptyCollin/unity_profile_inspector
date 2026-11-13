"""I17 修复循环决策测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_loop import Observation, decide_next  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("verified 停", decide_next(Observation("verified"), attempts=1,
                               max_attempts=3) == "verified")
ok("站点剩余 → 修站点", decide_next(Observation(
    "partial_fix", sites_remaining=2), attempts=1,
    max_attempts=3) == "fix_remaining_sites")
ok("无证据 → 补采", decide_next(Observation(
    "failed", reason="no_source_snippet"), attempts=1,
    max_attempts=3) == "request_evidence")
ok("同 diff 重复 → 换方案", decide_next(Observation(
    "needs_recheck", diff_hash="h1"), attempts=1, max_attempts=3,
    seen_hashes={"h1"}) == "alternative_patch")
ok("有改善 → 同根因细化", decide_next(Observation(
    "needs_recheck", metric_delta=-10), attempts=1,
    max_attempts=3) == "refine_same_root")
ok("无改善 → 换方案", decide_next(Observation(
    "needs_recheck"), attempts=1, max_attempts=3) == "alternative_patch")
ok("预算尽 → 回滚下一个", decide_next(Observation(
    "needs_recheck"), attempts=3, max_attempts=3) == "rollback_and_next")

print(f"\n==== 修复循环决策测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
