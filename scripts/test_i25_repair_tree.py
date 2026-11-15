"""I25：checkpoint 状态树与预算。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_tree import Budget, RepairTree  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 1. 改善保留
t = RepairTree(finding_id="f", budget=Budget(max_attempts_per_state=2,
                                             max_attempts_per_finding=4))
ok("root 初始", t.current_id == "root")
ok("improved_partial 保留", t.record("improved_partial", patch_id="p1") == "keep")
ok("创建子状态", t.current.depth == 1 and t.current.parent_id == "root")

# 2. 子状态失败两次 → 回退父
ok("子失败1 retry", t.record("no_effect") == "retry")
ok("子失败2 backtrack", t.record("no_effect") == "backtrack")
ok("回到子状态父", t.current_id == "root")

# 3. 根失败达到 finding 配额
t2 = RepairTree(finding_id="f", budget=Budget(max_attempts_per_state=2,
                                              max_attempts_per_finding=3))
r = [t2.record("no_effect") for _ in range(3)]
ok("根失败前两次 retry", r[:2] == ["retry", "retry"], r)
ok("第三次 finding_failed", r[2] == "finding_failed", r)
ok("root_failed", t2.root_failed() is True)

# 4. infra 不扣配额
t3 = RepairTree(finding_id="f", budget=Budget(max_attempts_per_finding=1))
ok("infra 返回重试", t3.record("infra_failure") == "infra_retry")
ok("infra 不扣 finding 失败", t3.finding_failures == 0, t3.finding_failures)

# 5. 全局预算
t4 = RepairTree(finding_id="f", budget=Budget(max_total_attempts=2,
                                              max_attempts_per_finding=99,
                                              max_attempts_per_state=99))
t4.record("no_effect")
ok("第二次预算耗尽", t4.record("no_effect") == "budget_exhausted")

# 6. 深度上限
t5 = RepairTree(finding_id="f", budget=Budget(max_depth=0))
ok("深度耗尽", t5.record("improved_partial") == "depth_exhausted")

print(f"\n==== I25 状态树测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
