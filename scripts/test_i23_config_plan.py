"""I23：ConfigProposal 只读生成。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.config_plan import propose  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
ps = TMP / "ProjectSettings"
ps.mkdir()
(ps / "QualitySettings.asset").write_text(
    "QualitySettings:\n  m_CurrentQuality: 1\n  m_QualitySettings:\n"
    "  - name: Mobile\n    shadowDistance: 80\n    antiAliasing: 2\n"
    "  - name: Low\n    shadowDistance: 20\n    antiAliasing: 0\n",
    encoding="utf-8")

items = propose(TMP)
fields = {(x.index, x.field, x.before, x.after) for x in items}
ok("shadowDistance 提议", (0, "shadowDistance", 80, 40) in fields, fields)
ok("antiAliasing 提议", (0, "antiAliasing", 2, 0) in fields, fields)
ok("低档不提议", not any(x.index == 1 for x in items), fields)
ok("默认不自动应用", all(hasattr(x, "reason") for x in items))

print(f"\n==== I23 配置提议测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
