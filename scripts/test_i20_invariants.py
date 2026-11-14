"""I20 机制不变量：从代码模式推导，不依赖类名。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mechanism_checks import invariant_ok  # noqa: E402
from upi.core.mechanism_infer import (infer_from_text, method_for_find)  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "G.cs"
f.parent.mkdir(parents=True)
f.write_text("class GameCore { void Tick(){ Canvas.ForceUpdateCanvases(); } }\n",
             encoding="utf-8")

hit = infer_from_text("Canvas.ForceUpdateCanvases();")
ok("从文本推出 canvas 不变量", hit is not None and hit[0] == "canvas_force_update")
method = method_for_find(TMP, "Assets/G.cs", "Canvas.ForceUpdateCanvases();")
ok("找到所在方法", method == "Tick", method)
ok_ok, _ = invariant_ok(TMP, "Assets/G.cs", method, hit[1])
ok("站点仍在 → false", ok_ok is False)

f.write_text("class GameCore { void Tick(){ } }\n", encoding="utf-8")
ok_ok, _ = invariant_ok(TMP, "Assets/G.cs", method, hit[1])
ok("站点清零 → true", ok_ok is True)

hit2 = infer_from_text("var b = new bool[N,N];")
ok("从文本推出 buffer 不变量", hit2 is not None and hit2[0] == "bool_buffer_alloc")

print(f"\n==== I20 机制不变量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
