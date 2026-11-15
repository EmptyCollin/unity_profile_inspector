"""I26 P6：无 importer 大资源分类。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.resource_policy import classify  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets"
assets.mkdir()
big = assets / "big.txt"
big.write_text("x" * 1000, encoding="utf-8")
code = assets / "Use.cs"
code.write_text('Resources.Load("big");', encoding="utf-8")

class U:
    targets = ["oversized_resource_asset:Assets/big.txt"]
    sites = ["Assets/big.txt"]

r = classify(TMP, U())
ok("被引用 → manual_review", r["status"] == "manual_review"
   and r["reason"] == "no_importer_and_referenced", r)
ok("记录引用", "Assets/Use.cs" in r["references"], r["references"])

code.write_text("nothing", encoding="utf-8")
r2 = classify(TMP, U())
ok("未引用 → 可删候选", r2["reason"] == "no_importer_unreferenced_can_delete", r2)

class Empty:
    targets = []
    sites = []

ok("无路径 no_plan", classify(TMP, Empty())["status"] == "no_plan")

print(f"\n==== I26 资源分类测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
