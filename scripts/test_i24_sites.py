"""I24 P4：机制站点清单。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mechanism_inventory import site_hints  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
src = TMP / "Assets"
src.mkdir()
f = src / "A.cs"
f.write_text(
    "class A {\n"
    "  void Build() {\n"
    "    var a = new Texture2D(1,1);\n"
    "    var b = new Texture2D(1,1);\n"
    "  }\n"
    "  void Other() {\n"
    "    var c = new Material(null);\n"
    "  }\n"
    "}\n", encoding="utf-8")

hints = site_hints(TMP, ["Assets/A.cs::Build"])
ok("Build 两个 Texture2D 站点", len([h for h in hints if "per_tile_texture" in h]) == 2, hints)
ok("不混入 Other", all("::Build" in h for h in hints), hints)
ok("空 scope 返回全部", len(site_hints(TMP, [])) == 3, site_hints(TMP, []))

print(f"\n==== I24 站点清单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
