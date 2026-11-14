"""I23：AssetInventory 只读盘点。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_inventory import duplicates, scan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets"
assets.mkdir()
p = assets / "big.txt"
p.write_text("x" * 100, encoding="utf-8")
p.with_name("big.txt.meta").write_text(
    "TextureImporter:\n  maxTextureSize: 4096\n  isReadable: 1\n",
    encoding="utf-8")
q = assets / "dup.txt"
q.write_text("x" * 100, encoding="utf-8")

items = scan(TMP, hash_assets=True)
ok("扫描到资产", len(items) == 2, len(items))
big = next(x for x in items if x.path.endswith("big.txt"))
ok("大小正确", big.bytes == 100, big.bytes)
ok("导入设置解析", big.importer.get("maxTextureSize") == 4096, big.importer)
ok("重复 hash 检出", len(duplicates(items)) == 1, duplicates(items))
ok("无 hash 不做重复", duplicates(scan(TMP)) == {})

print(f"\n==== I23 资源盘点测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
