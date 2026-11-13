"""I16 多病例执行器测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.multi_repair import run_queue  # noqa: E402
from upi.core.repair_queue import RepairQueue  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


q = RepairQueue()
for i in range(11):
    q.add(f"c{i:02d}", f"f{i}")

applied, rolled = set(), []


def plan(c):
    return c.case_id not in ("c09",)


def apply(c):
    applied.add(c.case_id)
    return True


def verify(c):
    if c.case_id in ("c09",):
        return "needs_recheck"
    if c.case_id == "c10":
        return "partial_fix"
    return "verified"


def rollback(c):
    rolled.append(c.case_id)
    return True


r = run_queue(q, plan=plan, apply=apply, verify=verify, rollback=rollback)
s = r.summary()
ok("9 个 verified", s["verified"] == 9, s)
ok("no_plan 病例不计 attempts 成功", q.get("c09").status == "failed")
ok("partial_fix 单独记", q.get("c10").status == "partial_fix")
ok("未应用病例无需回滚", set(rolled) == {"c10"}, rolled)
ok("其它病例不受影响", q.get("c00").status == "verified")

print(f"\n==== 多病例执行器测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
