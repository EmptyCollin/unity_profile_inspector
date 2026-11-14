"""I22：action ablation 与静态锚点可迁移性。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench.anti_cheat import remove_action  # noqa: E402
from upi.core.mechanism_inventory import scan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


spec = {"commands": [
    {"type": "start_game"}, {"type": "set_board"},
    {"type": "open_shop"}, {"type": "stop"}]}
out = remove_action(spec, "open_shop")
ok("删除 open_shop", [c["type"] for c in out["commands"]] ==
   ["start_game", "set_board", "stop"], out["commands"])
ok("原 spec 不变", len(spec["commands"]) == 4)
ok("notes 标记 ablation", "ablate" in out["notes"])

try:
    remove_action(spec, "resolve")
    ok("缺 action 应报错", False)
except ValueError:
    ok("缺 action 报错", True)

two = {"commands": [{"type": "open_shop"}, {"type": "wait"},
                    {"type": "open_shop"}]}
out2 = remove_action(two, "open_shop", -1)
ok("删除最后一个 open_shop", [c["type"] for c in out2["commands"]] ==
   ["open_shop", "wait"], out2["commands"])
out3 = remove_action(two, "open_shop", all_occurrences=True)
ok("删除全部 open_shop", [c["type"] for c in out3["commands"]] == ["wait"],
   out3["commands"])

# 静态锚点迁移：同一机制换方法名，扫描应跟随
TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets"
assets.mkdir()
f = assets / "Hot.cs"
f.write_text("class Hot { void MethodA() { var t = new Texture2D(1,1); } }\n",
             encoding="utf-8")
sites = scan(TMP).by_mechanism().get("per_tile_texture", [])
ok("静态扫描命中 MethodA",
   bool(sites) and sites[0].method == "MethodA", sites)

f.write_text("class Hot { void MethodB() { var t = new Texture2D(1,1); } }\n",
             encoding="utf-8")
sites2 = scan(TMP).by_mechanism().get("per_tile_texture", [])
ok("方法改名后跟随 MethodB",
   bool(sites2) and sites2[0].method == "MethodB", sites2)

f.write_text("class Hot { void MethodB() { var x = 1; } }\n",
             encoding="utf-8")
ok("clean 文件无站点",
   not scan(TMP).by_mechanism().get("per_tile_texture", []))

print(f"\n==== I22 action ablation 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
