"""I16 多病例队列测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_queue import RepairQueue  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


q = RepairQueue()
for i in range(11):
    q.add(f"c{i:02d}", f"f{i}")
ok("11 病例入队", q.summary()["total"] == 11)
first = q.next_pending()
ok("取第一个", first is not None and first.case_id == "c00")
q.mark("c00", "verified", verdict="verified")
ok("verified 不再取", q.next_pending().case_id == "c01")
q.mark("c01", "failed", reason="no patch")
ok("failed 不再取", q.next_pending().case_id == "c02")
q.get("c02").attempts = 3
ok("预算耗尽跳过", q.next_pending().case_id == "c03")
q.mark("c03", "partial_fix", reason="sites")
ok("partial 算未完成", not q.target_met())
for i in range(3, 11):
    q.mark(f"c{i:02d}", "verified")
ok("9 个 verified 达标", q.summary()["verified"] == 9 and q.target_met())

print(f"\n==== 多病例队列测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
