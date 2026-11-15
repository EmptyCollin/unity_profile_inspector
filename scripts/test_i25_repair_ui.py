"""I25：修复 UI 只读数据。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.repair_ui import (REPAIR_HTML, blacklist_entries,  # noqa: E402
                                  budget, trees)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("页面含状态树接口", "/api/repair/trees" in REPAIR_HTML)
ok("页面含黑名单接口", "/api/blacklist" in REPAIR_HTML)
ok("页面含模式标记", "production" in REPAIR_HTML)
ok("页面含 token 占位", "__UPI_TOKEN__" in REPAIR_HTML)

b = budget()
ok("预算字段", b["max_attempts_per_finding"] == 6 and b["max_total_attempts"] == 12, b)
ok("树读取不炸", isinstance(trees(), list))
ok("黑名单读取不炸", isinstance(blacklist_entries(), list))

print(f"\n==== I25 UI 只读测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
