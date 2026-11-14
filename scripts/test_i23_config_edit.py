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
original = "QualitySettings:\n  m_CurrentQuality: 2\n  m_TextureQuality: 0\n"
f.write_text(original, encoding="utf-8")

edit = ConfigEdit("ProjectSettings/QualitySettings.asset",
                  "m_TextureQuality", 0, 1)
ok("validate 通过", validate(TMP, [edit]) == [], validate(TMP, [edit]))
done = apply(TMP, [edit])
ok("字段已改", "m_TextureQuality: 1" in f.read_text(encoding="utf-8"))
rollback(TMP, done)
ok("rollback 恢复", f.read_text(encoding="utf-8") == original)

ok("不存在字段被拒", validate(TMP, [ConfigEdit(
    "ProjectSettings/QualitySettings.asset", "nope", 0, 1)]) != [])
ok("越界路径被拒", validate(TMP, [ConfigEdit(
    "Assets/Other.asset", "m_TextureQuality", 0, 1)]) != [])

print(f"\n==== I23 配置编辑测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
