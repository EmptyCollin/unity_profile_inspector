"""I20 静态机制站点清单测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mechanism_inventory import scan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "Game.cs"
f.parent.mkdir(parents=True)
f.write_text("""public class Game {
  void BuildBoard() {
    img.material = new Material(img.material);
    var t = new Texture2D(2, 2);
  }
  void OpenShop() {
    var a = Resources.Load<TextAsset>("x");
  }
  void Update() {
    for (int i=0;i<4;i++) model.HasMatch();
  }
}
""", encoding="utf-8")

inv = scan(TMP)
by = inv.by_mechanism()
ok("BuildBoard 材质站点", "per_instance_material" in by)
ok("Texture2D 站点", "per_tile_texture" in by)
ok("Resources.Load 站点", "sync_resources_load" in by)
ok("HasMatch 站点", "idle_hint_scan" in by)
ok("站点带方法", any(s.method == "BuildBoard" for s in by["per_instance_material"]))

print(f"\n==== I20 站点清单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
