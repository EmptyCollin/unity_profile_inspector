"""I20 不变量：执行 planner 声明，不在 verifier 里推导模式。"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.mechanism_checks import invariant_ok  # noqa: E402
from upi.core.mechanism_infer import method_for_find  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "G.cs"
f.parent.mkdir(parents=True)
f.write_text("class GameCore { void Tick(){ Canvas.ForceUpdateCanvases(); } }\n",
             encoding="utf-8")

find = "Canvas.ForceUpdateCanvases();"
method = method_for_find(TMP, "Assets/G.cs", find)
ok("定位 find 所在方法", method == "Tick", method)

pattern = r"ForceUpdateCanvases\s*\("
ok("声明的 pattern 命中 find", re.search(pattern, find) is not None)
ok("站点仍在 → false", invariant_ok(TMP, "Assets/G.cs", method, pattern)[0] is False)

f.write_text("class GameCore { void Tick(){ } }\n", encoding="utf-8")
ok("站点清零 → true", invariant_ok(TMP, "Assets/G.cs", method, pattern)[0] is True)

# 声明必须来自 find，不能凭空造
fake = r"Resources\.Load"
ok("pattern 不在 find 中 → 声明无效", re.search(fake, find) is None)

print(f"\n==== I20 机制不变量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
