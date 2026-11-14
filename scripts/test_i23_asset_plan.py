"""I23：AssetEditProposal 只读生成。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_plan import propose  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets"
assets.mkdir()
p = assets / "big.png"
p.write_bytes(b"x" * 10)
p.with_name("big.png.meta").write_text(
    "TextureImporter:\n  maxTextureSize: 4096\n  isReadable: 1\n  mipmaps: 1\n",
    encoding="utf-8")
items = propose(TMP)
fields = {(x.asset_path, x.field) for x in items}
ok("maxTextureSize 提议", ("Assets/big.png", "maxTextureSize") in fields, fields)
ok("isReadable 提议", ("Assets/big.png", "isReadable") in fields, fields)
ok("默认不自动应用（仅 proposal）",
   all(hasattr(x, "perception_risk") for x in items))

small = assets / "small.png"
small.write_bytes(b"y")
small.with_name("small.png.meta").write_text(
    "TextureImporter:\n  maxTextureSize: 512\n", encoding="utf-8")
items2 = propose(TMP)
ok("小纹理不提议 maxTextureSize",
   not any(x.asset_path.endswith("small.png") and x.field == "maxTextureSize"
           for x in items2))

print(f"\n==== I23 资源提议测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
