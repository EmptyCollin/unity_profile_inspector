"""I25：逐 finding 串行修复循环。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_serial import run_repair_loop  # noqa: E402
from upi.core.repair_tree import Budget, RepairTree  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


statuses = iter(["improved_partial", "no_effect", "improved_partial", "verified"])
rolled = []
refs = iter(["r1", "r2"])


def plan(state):
    return object()


def apply(p):
    return True


def comp():
    return True


def capture():
    return {}


def verify(state, obs):
    try:
        return next(statuses)
    except StopIteration:
        return "verified"


def rollback(p):
    rolled.append(p)
    return True


def git_head():
    try:
        return next(refs)
    except StopIteration:
        return "r3"


tree = RepairTree(finding_id="f", budget=Budget(
    max_depth=4, max_attempts_per_state=2, max_attempts_per_finding=4,
    max_total_attempts=8))
r = run_repair_loop(tree, plan=plan, apply=apply, compile_fn=comp,
                    capture=capture, verify=verify, rollback=rollback,
                    git_head=git_head, max_steps=4)
ok("4 次 attempt", r.attempts == 4, r)
ok("keep/retry 序列", r.actions[:3] == ["keep", "retry", "keep"], r.actions)
ok("partial 不回滚", len(rolled) == 1, rolled)
ok("保留子状态", tree.current.depth == 3, tree.current.depth)
ok("git_ref 写入", tree.states["s1"].git_ref == "r1" and
   tree.states["s2"].git_ref == "r2", tree.states)

# 子状态连续失败 → 回退父
tree2 = RepairTree(finding_id="g", budget=Budget(
    max_depth=2, max_attempts_per_state=2, max_attempts_per_finding=4,
    max_total_attempts=8))
seq = iter(["improved_partial", "no_effect", "no_effect"])


def verify2(state, obs):
    try:
        return next(seq)
    except StopIteration:
        return "no_effect"


r2 = run_repair_loop(tree2, plan=plan, apply=apply, compile_fn=comp,
                     capture=capture, verify=verify2, rollback=rollback,
                     git_head=lambda: "r1", max_steps=2)
ok("子失败回退父", tree2.current_id == "s1", tree2.current_id)
ok("父状态保留", tree2.states["s1"].git_ref == "r1", tree2.states)

print(f"\n==== I25 串行循环测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
