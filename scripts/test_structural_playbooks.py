"""机制级结构回退测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.structural_playbooks import propose, to_plan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "T.cs"
f.parent.mkdir(parents=True)
f.write_text("""public class T {
  void RefreshTiles() {
    var tmpTex = new Texture2D(2, 2);
    tmpTex.Apply();
    if (tmpTex.width < 0) return;
  }
}
""", encoding="utf-8")
edits = propose(TMP, ["Assets/T.cs::RefreshTiles"])
ok("临时纹理可删", len(edits) == 1 and "new Texture2D" in edits[0]["find"], edits)
ok("结构 plan", to_plan(edits).playbook_id == "PB-STRUCT")

print(f"\n==== 结构回退测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
