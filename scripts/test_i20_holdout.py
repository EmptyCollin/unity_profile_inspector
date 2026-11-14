"""I20 holdout：重命名类/方法后，机制推导仍成立。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mechanism_checks import invariant_ok  # noqa: E402
from upi.core.mechanism_infer import (infer_from_text, method_for_find,  # noqa: E402
                                      metric_for_text)

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

hit = infer_from_text("var t = new Texture2D(2,2);")
ok("重命名后仍识别纹理", hit and hit[0] == "per_tile_texture")
ok("metric=memory", metric_for_text("new Texture2D(2,2)") == "memory",
   metric_for_text("new Texture2D(2,2)"))
m = method_for_find(TMP, "Assets/Renamed.cs", "new Texture2D")
ok("方法= Tick（无类名依赖）", m == "Tick", m)
ok("站点未清 → false", invariant_ok(TMP, "Assets/Renamed.cs", m, hit[1])[0] is False)

f.write_text("""class GameCore {
  void Tick() { }
  void Open() {
    var x = Resources.Load<TextAsset>("payload");
  }
}
""", encoding="utf-8")
ok("清理后 → true", invariant_ok(TMP, "Assets/Renamed.cs", m, hit[1])[0] is True)

hit2 = infer_from_text('Resources.Load<TextAsset>("payload")')
ok("重命名后仍识别加载", hit2 and hit2[0] == "sync_resources_load")

# 产品模块不得含测试类名
for rel in ("upi/core/targets.py", "upi/core/mechanism_checks.py",
            "upi/core/mechanism_infer.py"):
    text = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel} 无 ChromaGame/BoardModel",
       "ChromaGame" not in text and "BoardModel" not in text, rel)

print(f"\n==== I20 holdout 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
