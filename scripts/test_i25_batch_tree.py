"""I25：单 unit batch 接入 RepairTree。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.batch_round import run_batch  # noqa: E402
from upi.core.mechanism_batch import consolidate, PatchRef  # noqa: E402
from upi.core.repair_tree import Budget, RepairTree  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


units = consolidate([{"finding_id": "f", "mechanism": "m",
                      "anchors": [{"file": "A.cs", "method": "A"}]}])
unit = units[0]
rolled = []


def plan(u):
    return PatchRef(u.unit_id, ["A.cs"])


def apply(p):
    return True


def verify(u, obs):
    return "improved_partial"


tree = RepairTree(finding_id="f", budget=Budget())

r = run_batch(units, plan=plan, apply=apply, compile_fn=lambda: True,
              capture=lambda: {}, verify=verify,
              rollback=lambda p: rolled.append(p.unit_id) or True,
              tree=tree)
ok("partial 保留", r.partial == [unit.unit_id], r.partial)
ok("partial 不回滚", rolled == [], rolled)
ok("tree 子状态", tree.current.depth == 1, tree.current_id)

# 失败路径：回滚 + tree 失败
rolled2 = []
r2 = run_batch(units, plan=plan, apply=apply, compile_fn=lambda: True,
               capture=lambda: {}, verify=lambda u, o: "no_effect",
               rollback=lambda p: rolled2.append(p.unit_id) or True,
               tree=RepairTree(finding_id="f", budget=Budget()))
ok("no_effect 回滚", r2.rolled_back == [unit.unit_id], r2.rolled_back)
ok("no_effect 不计 verified", r2.verified == [], r2.verified)

# finding_failed 回调
calls = []
t3 = RepairTree(finding_id="f", budget=Budget(
    max_attempts_per_finding=1, max_attempts_per_state=1,
    max_total_attempts=2, max_depth=1))
r3 = run_batch(units, plan=plan, apply=apply, compile_fn=lambda: True,
               capture=lambda: {}, verify=lambda u, o: "no_effect",
               rollback=lambda p: True, tree=t3,
               on_failure=lambda uid, verdict, tree: calls.append(
                   (uid, verdict, tree.root_failed())))
ok("root failure 回调", calls and calls[0][0] == unit.unit_id and
   calls[0][2] is True, calls)

print(f"\n==== I25 batch tree 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
