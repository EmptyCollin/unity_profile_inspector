"""I20 批量归因测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.patch_attribution import (PatchTargets, TargetOutcome,  # noqa: E402
                                       attribute)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


patches = [PatchTargets("u1", ["t1"]), PatchTargets("u2", ["t2"]),
           PatchTargets("u3", ["t3"])]
out = [TargetOutcome("t1", "verified"),
       TargetOutcome("t2", "partial"),
       TargetOutcome("t3", "verified")]
r = attribute(patches, out)
ok("verified 保留 u1/u3", r.keep == ["u1", "u3"], r)
ok("failed 只回滚 u2", r.rollback == ["u2"], r)
ok("无异常不分组", not r.needs_group_test)

r2 = attribute(patches, [TargetOutcome("t1", "needs_recheck", new_cluster=True,
                                       owner="u1")])
ok("新 cluster → 异常", r2.needs_group_test and r2.anomaly == ["t1"], r2)
ok("异常补丁回滚", "u1" in r2.rollback, r2)

print(f"\n==== I20 批量归因测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
