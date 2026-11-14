"""I23：SceneConfig 只读解析。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.scene_config import scan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
assets = TMP / "Assets" / "Scenes"
assets.mkdir(parents=True)
f = assets / "Main.unity"
f.write_text("""--- !u!1 &1
GameObject:
  m_Name: Dir
--- !u!108 &2
Light:
  m_Type: 1
  m_Intensity: 1.2
  m_Shadows:
    m_Type: 2
--- !u!20 &3
Camera:
  m_FieldOfView: 60
""", encoding="utf-8")
scenes = scan(TMP)
ok("扫到场景", len(scenes) == 1)
comps = scenes[0].components
ok("Light 解析", any(c.type == "Light" for c in comps), comps)
light = next(c for c in comps if c.type == "Light")
ok("Light 字段", light.fields.get("m_Intensity") == "1.2", light.fields)
ok("Camera 解析", any(c.type == "Camera" for c in comps))

print(f"\n==== I23 场景配置测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
