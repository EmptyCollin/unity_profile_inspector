"""I26 P6：资产 unit attempt。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_exam import attempt_asset_unit  # noqa: E402
from upi.core.asset_candidate import rollback_candidate  # noqa: E402

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

class U:
    targets = ["oversized_resource_asset:Assets/big.png"]
    sites = ["Assets/big.png"]

r = attempt_asset_unit(TMP, U(), measure_before=lambda: 100,
                       measure_after=lambda: 80,
                       eval_fn=lambda path: None)
ok("improved", r["status"] == "improved" and r["rolled_back"] is False, r)
rollback_candidate(TMP, r["done"])

r2 = attempt_asset_unit(TMP, U(), measure_before=lambda: 100,
                        measure_after=lambda: 120,
                        eval_fn=lambda path: None)
ok("no_effect rollback", r2["status"] == "no_effect"
   and r2["rolled_back"] is True, r2)
ok("meta 回滚", "maxTextureSize: 4096" in
   a.with_name("big.png.meta").read_text(encoding="utf-8"))

class NoProposal:
    targets = ["oversized_resource_asset:Assets/none.png"]
    sites = ["Assets/none.png"]

r3 = attempt_asset_unit(TMP, NoProposal())
ok("无 proposal no_plan", r3["status"] == "no_plan", r3)

print(f"\n==== I26 资产 attempt 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
