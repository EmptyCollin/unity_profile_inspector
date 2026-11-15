"""I25-ext：RepairEngine 边界。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_engine import SerialEngine, tree_for_run_state  # noqa: E402
from upi.core.repair_tree import Budget, RepairTree  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


class P:
    unit_id = "u1"


rolled = []
engine = SerialEngine(RepairTree(finding_id="f", budget=Budget(max_depth=3)))
statuses = iter(["improved_partial", "no_effect"])


def verify(obs):
    return next(statuses)


a1 = engine.attempt(P(), apply=lambda p: True, compile_fn=lambda: True,
                    capture=lambda: {}, verify=verify,
                    rollback=lambda p: rolled.append("x") or True,
                    git_head=lambda: "ref1")
ok("partial → keep", a1 == "keep", a1)
ok("partial 不回滚", rolled == [], rolled)
ok("tree 有 git_ref", engine.tree.current.git_ref == "ref1",
   engine.tree.current.git_ref)

a2 = engine.attempt(P(), apply=lambda p: True, compile_fn=lambda: True,
                    capture=lambda: {}, verify=verify,
                    rollback=lambda p: rolled.append("y") or True,
                    git_head=lambda: "ref2")
ok("no_effect → retry", a2 == "retry", a2)
ok("失败回滚一次", rolled == ["y"], rolled)

ok("tree_dict 可序列化", "states" in engine.tree_dict())

t2 = tree_for_run_state({"run_id": "r1", "project": "P",
                         "budget": {"max_attempts_per_issue": 3},
                         "spent": {"attempts": 1}})
ok("预算剩余 = 2", t2.budget.max_attempts_per_finding == 2,
   t2.budget.max_attempts_per_finding)
ok("total 预算 = 2", t2.budget.max_total_attempts == 2)
ok("project_root 传递", t2.project_root == "P")

print(f"\n==== I25-ext Engine 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
