"""I23：ConfigSnapshot 只读解析。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.config_snapshot import diff, scan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
ps = TMP / "ProjectSettings"
ps.mkdir()
f = ps / "QualitySettings.asset"
f.write_text("QualitySettings:\n  m_CurrentQuality: 2\n  m_TextureQuality: 0\n",
             encoding="utf-8")
items = scan(TMP)
ok("扫到配置文件", len(items) == 1, len(items))
ok("字段解析", items[0].fields.get("m_CurrentQuality") == "2",
   items[0].fields)

f.write_text("QualitySettings:\n  m_CurrentQuality: 1\n  m_TextureQuality: 0\n",
             encoding="utf-8")
d = diff(items, scan(TMP))
ok("diff 检出字段变化", d.get("ProjectSettings/QualitySettings.asset",
                              {}).get("m_CurrentQuality") == ["2", "1"], d)

print(f"\n==== I23 配置快照测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
