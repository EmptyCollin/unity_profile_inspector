"""I23：ConfigEditPlan 最小执行器。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.config_edit import ConfigEdit, apply, rollback, validate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
ps = TMP / "ProjectSettings"
ps.mkdir()
f = ps / "QualitySettings.asset"
original = ("QualitySettings:\n  m_CurrentQuality: 2\n  m_QualitySettings:\n"
            "  - name: Mobile\n    shadowDistance: 40\n    antiAliasing: 0\n"
            "  - name: High\n    shadowDistance: 80\n    antiAliasing: 2\n")
f.write_text(original, encoding="utf-8")

edit = ConfigEdit("ProjectSettings/QualitySettings.asset",
                  "m_CurrentQuality", 2, 1)
ok("validate 通过", validate(TMP, [edit]) == [], validate(TMP, [edit]))
done = apply(TMP, [edit])
ok("字段已改", "m_CurrentQuality: 1" in f.read_text(encoding="utf-8"))
rollback(TMP, done)
ok("rollback 恢复", f.read_text(encoding="utf-8") == original)

nested = ConfigEdit("ProjectSettings/QualitySettings.asset",
                    "shadowDistance", 80, 40,
                    section="m_QualitySettings", index=1)
ok("嵌套字段 validate 通过", validate(TMP, [nested]) == [],
   validate(TMP, [nested]))
done2 = apply(TMP, [nested])
text2 = f.read_text(encoding="utf-8")
ok("嵌套字段只改 High", "name: High\n    shadowDistance: 40" in text2,
   text2)
ok("Mobile 不变", "name: Mobile\n    shadowDistance: 40" in text2)
rollback(TMP, done2)
ok("嵌套 rollback 恢复", f.read_text(encoding="utf-8") == original)

ok("不存在字段被拒", validate(TMP, [ConfigEdit(
    "ProjectSettings/QualitySettings.asset", "nope", 0, 1)]) != [])
ok("越界路径被拒", validate(TMP, [ConfigEdit(
    "Assets/Other.asset", "m_TextureQuality", 0, 1)]) != [])

print(f"\n==== I23 配置编辑测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
