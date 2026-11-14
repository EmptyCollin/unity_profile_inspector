"""I23：感知门禁（Pillow 像素差；缺依赖时 hash fallback）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.perception import compare, gate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
a = TMP / "a.png"
b = TMP / "b.png"
c = TMP / "c.png"
try:
    from PIL import Image
    Image.new("RGB", (32, 32), (0, 0, 0)).save(a)
    Image.new("RGB", (32, 32), (0, 0, 0)).save(b)
    Image.new("RGB", (32, 32), (255, 255, 255)).save(c)
    pixel_mode = True
except Exception:  # noqa: BLE001
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    c.write_bytes(b"different")
    pixel_mode = False

r = compare(a, b)
ok("同图 changed=False", r["changed"] is False, r)
ok("同图 gate 通过", gate(a, b)["passed"] is True)
r2 = gate(a, c)
ok("差异 → 不通过", r2["passed"] is False, r2)
ok("差异要求人工", r2["requires_human"] is True)
if pixel_mode:
    ok("像素模式", r["mean_abs"] == 0 and r.get("size") == [32, 32], r)

print(f"\n==== I23 感知门禁测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
