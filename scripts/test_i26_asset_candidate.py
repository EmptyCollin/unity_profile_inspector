"""I26 P6：资产候选闭环。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_candidate import (apply_candidate, propose_for,  # noqa: E402
                                      rollback_candidate, to_edit)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
a = TMP / "Assets" / "big.png"
a.parent.mkdir(parents=True)
a.write_bytes(b"x")
a.with_name("big.png.meta").write_text(
    "TextureImporter:\n  maxTextureSize: 4096\n  isReadable: 1\n",
    encoding="utf-8")

p = propose_for(TMP, "Assets/big.png")
ok("找到资产 proposal", p is not None and p.field == "maxTextureSize",
   p)
if p:
    e = to_edit(p)
    ok("转 AssetEdit", e.asset_path == "Assets/big.png"
       and e.after == 2048, e)
    done = apply_candidate(TMP, p, eval_fn=lambda path: None)
    txt = a.with_name("big.png.meta").read_text(encoding="utf-8")
    ok("应用 meta", "maxTextureSize: 2048" in txt, txt)
    rollback_candidate(TMP, done)
    ok("回滚 meta", "maxTextureSize: 4096" in
       a.with_name("big.png.meta").read_text(encoding="utf-8"))

ok("未知资产无 proposal", propose_for(TMP, "Assets/none.png") is None)

print(f"\n==== I26 资产候选测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
