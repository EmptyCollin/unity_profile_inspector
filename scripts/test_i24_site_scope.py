"""I24 P4：全 scope 站点残留。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.site_scope import sites_remaining_in_scopes  # noqa: E402

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
    "  void Hud() {\n"
    "    var c = new Texture2D(1,1);\n"
    "  }\n"
    "}\n", encoding="utf-8")
diffs = [{"file": "Assets/A.cs",
          "find": "var a = new Texture2D(1,1);",
          "replace": "var a = _tex;"}]
sa = sites_remaining_in_scopes(TMP, diffs, ["Assets/A.cs::Build",
                                           "Assets/A.cs::Hud"])
keys = [k for k in sa if "Texture2D" in k]
ok("Build 残留一个", bool(keys) and
   "Assets/A.cs::Build" in sa[keys[0]], sa)
ok("Hud 也覆盖", bool(keys) and "Assets/A.cs::Hud" in sa[keys[0]], sa)
sa2 = sites_remaining_in_scopes(TMP, diffs, ["Assets/A.cs::Build"])
keys2 = [k for k in sa2 if "Texture2D" in k]
ok("只给 Build 不混 Hud", bool(keys2) and
   "Assets/A.cs::Hud" not in sa2[keys2[0]], sa2)

print(f"\n==== I24 全 scope 站点测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
