"""I25：修复 UI 只读数据。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.repair_ui import (REPAIR_HTML, audit_entries,  # noqa: E402
                                  blacklist_entries, budget, mode, rollback_tree,
                                  skips, trees)
from upi.core.repair_tree import Budget, RepairTree, save_tree  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("页面含状态树接口", "/api/repair/trees" in REPAIR_HTML)
ok("页面含黑名单接口", "/api/blacklist" in REPAIR_HTML)
ok("页面含模式标记", "production" in REPAIR_HTML)
ok("页面含 token 占位", "__UPI_TOKEN__" in REPAIR_HTML)
ok("页面含黑名单写操作", "addBL" in REPAIR_HTML and "setBL" in REPAIR_HTML)
ok("页面含预算保存", "saveBudget" in REPAIR_HTML)
ok("页面含审计", "/api/repair/audit" in REPAIR_HTML and "audit" in REPAIR_HTML)
ok("审计读取不炸", isinstance(audit_entries(), list))
ok("命中读取不炸", isinstance(skips(), list))
ok("页面含命中", "/api/repair/skips" in REPAIR_HTML)
ok("页面含模式接口", "/api/repair/mode" in REPAIR_HTML)
ok("默认 production", mode() == "production", mode())

b = budget()
ok("预算字段", b["max_attempts_per_finding"] == 6 and b["max_total_attempts"] == 12, b)
ok("树读取不炸", isinstance(trees(), list))
ok("黑名单读取不炸", isinstance(blacklist_entries(), list))

t = RepairTree(finding_id="f", budget=Budget())
t.record("improved_partial", patch_id="p1")
p = save_tree(t, Path(tempfile.mkdtemp()) / "repair_tree.json")
r = rollback_tree(p, "root")
ok("回滚指针", r["ok"] and r["pointer_only"] and r["to"] == "root", r)
ok("回滚已落盘", '"current": "root"' in p.read_text(encoding="utf-8"))
ok("审计含回滚结果", any("pointer_only=" in str(x.get("detail", ""))
                       for x in audit_entries()))

print(f"\n==== I25 UI 只读测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
