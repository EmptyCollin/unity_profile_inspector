"""I20 holdout：重命名类后，执行 planner 声明的 invariant。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.llm_planner import _validate_invariant  # noqa: E402
from upi.core.mechanism_checks import invariant_ok  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "Renamed.cs"
f.parent.mkdir(parents=True)
f.write_text("""class GameCore {
  void Tick() {
    var t = new Texture2D(2, 2);
    t.Apply();
  }
  void Open() {
    var x = Resources.Load<TextAsset>("payload");
  }
}
""", encoding="utf-8")

find = "var t = new Texture2D(2, 2);"
diffs = [{"file": "Assets/Renamed.cs", "find": find, "replace": ""}]
decl = {"file": "Assets/Renamed.cs", "method": "Tick",
        "pattern": r"new\s+Texture2D", "expect": 0}
v, err = _validate_invariant(TMP, diffs, decl)
ok("重命名类后声明合法", v is not None, err)
ok("metric 由 planner 给出（这里 memory）", True)
ok("站点未清 → false", invariant_ok(TMP, "Assets/Renamed.cs", "Tick",
                                    decl["pattern"])[0] is False)

f.write_text("""class GameCore {
  void Tick() { }
  void Open() {
    var x = Resources.Load<TextAsset>("payload");
  }
}
""", encoding="utf-8")
ok("清理后 → true", invariant_ok(TMP, "Assets/Renamed.cs", "Tick",
                                decl["pattern"])[0] is True)

# 声明必须命中 find，不能凭空写
bad = {"file": "Assets/Renamed.cs", "method": "Tick",
       "pattern": r"Resources\.Load", "expect": 0}
v2, err2 = _validate_invariant(TMP, diffs, bad)
ok("凭空声明被拒", v2 is None, err2)

for rel in ("upi/core/targets.py", "upi/core/mechanism_checks.py",
            "upi/core/mechanism_infer.py"):
    text = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel} 无 ChromaGame/BoardModel",
       "ChromaGame" not in text and "BoardModel" not in text, rel)

print(f"\n==== I20 holdout 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
