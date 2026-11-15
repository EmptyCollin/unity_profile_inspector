"""I26-ext E5：patch 形状。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.patch_shape import classify  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("空 diffs", classify([]) == "empty")
ok("空 replace → semantic_delete",
   classify([{"find": "x()", "replace": ""}]) == "semantic_delete")
ok("大块删除 → semantic_delete",
   classify([{"find": "x" * 500, "replace": "y"}]) == "semantic_delete")
ok("新增字段 → structural",
   classify([{"find": "a", "replace": "private int c; a"}]) == "structural")
ok("缓存 → structural",
   classify([{"find": "a", "replace": "var cache = a; a"}]) == "structural")
ok("删调用 → micro",
   classify([{"find": "line1\nline2\nline3", "replace": "line1"}]) == "micro")
ok("未知", classify([{"find": "a", "replace": "b"}]) == "unknown")

print(f"\n==== I26-ext patch shape 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
