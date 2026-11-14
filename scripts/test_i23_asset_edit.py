"""I23：AssetEditPlan 最小执行器。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_edit import AssetEdit, apply, rollback, validate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets"
assets.mkdir()
p = assets / "big.png"
p.write_bytes(b"x")
meta = p.with_name("big.png.meta")
original = "TextureImporter:\n  maxTextureSize: 4096\n  isReadable: 1\n"
meta.write_text(original, encoding="utf-8")

edits = [AssetEdit("Assets/big.png", "maxTextureSize", 4096, 2048),
         AssetEdit("Assets/big.png", "isReadable", 1, 0)]
ok("validate 通过", validate(TMP, edits) == [], validate(TMP, edits))

done = apply(TMP, edits)
text = meta.read_text(encoding="utf-8")
ok("maxTextureSize 已改", "maxTextureSize: 2048" in text, text)
ok("isReadable 已改", "isReadable: 0" in text, text)

rollback(TMP, done)
ok("rollback 恢复原文", meta.read_text(encoding="utf-8") == original)

bad = [AssetEdit("Assets/big.png", "compressionFormat", 0, 1)]
ok("未允许字段被拒", validate(TMP, bad) != [])
bad2 = [AssetEdit("../outside.png", "maxTextureSize", 1, 2)]
ok("越界路径被拒", validate(TMP, bad2) != [])

print(f"\n==== I23 资源编辑执行器测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
