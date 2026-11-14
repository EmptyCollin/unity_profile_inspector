"""I23：批量 AssetEditPlan。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.apply_asset_plans import from_proposals  # noqa: E402
from upi.core.asset_edit import apply_and_reimport, rollback, validate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets"
assets.mkdir()
for name in ("a.png", "b.png"):
    p = assets / name
    p.write_bytes(b"x")
    p.with_name(name + ".meta").write_text(
        "TextureImporter:\n  maxTextureSize: 4096\n  isReadable: 1\n",
        encoding="utf-8")

doc = {"proposals": [
    {"asset_path": "Assets/a.png", "field": "maxTextureSize",
     "before": 4096, "after": 2048},
    {"asset_path": "Assets/b.png", "field": "isReadable",
     "before": 1, "after": 0},
]}
edits = from_proposals(doc["proposals"])
ok("提案转 edit", len(edits) == 2)
ok("批量 validate 通过", validate(TMP, edits) == [], validate(TMP, edits))

calls = []
done = apply_and_reimport(TMP, edits,
                          eval_fn=lambda p: calls.append(p))
ok("两个重导入", calls == ["Assets/a.png", "Assets/b.png"], calls)
ok("a maxTextureSize", "maxTextureSize: 2048" in
   (assets / "a.png.meta").read_text(encoding="utf-8"))
ok("b isReadable", "isReadable: 0" in
   (assets / "b.png.meta").read_text(encoding="utf-8"))
rollback(TMP, done)
ok("批量 rollback", "maxTextureSize: 4096" in
   (assets / "a.png.meta").read_text(encoding="utf-8"))

print(f"\n==== I23 资产批量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
