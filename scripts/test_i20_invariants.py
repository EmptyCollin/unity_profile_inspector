"""I20 机制不变量检查测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mechanism_checks import invariant_ok  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "G.cs"
f.parent.mkdir(parents=True)
f.write_text("void Update(){ Canvas.ForceUpdateCanvases(); }\n", encoding="utf-8")
ok("ForceUpdate 仍在 → false", invariant_ok(TMP, "hot_function:ChromaGame.Update")[0] is False)
f.write_text("void Update(){ }\n", encoding="utf-8")
ok("ForceUpdate 清零 → true", invariant_ok(TMP, "hot_function:ChromaGame.Update")[0] is True)

f.write_text("void X(){ new bool[N,N]; }\n", encoding="utf-8")
ok("bool buffer 仍在 → false", invariant_ok(TMP, "hot_function:BoardModel.FindMatches")[0] is False)
f.write_text("void X(){ }\n", encoding="utf-8")
ok("bool buffer 清零 → true", invariant_ok(TMP, "hot_function:BoardModel.FindMatches")[0] is True)

print(f"\n==== I20 机制不变量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
