"""I26 P5：baseline 缓存。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.baseline_cache import get, key, put  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
ok("miss", get("proto", TMP) is None)
p = put("proto", {"roots": ["A"], "runs": 5}, TMP)
ok("hit", get("proto", TMP)["data"]["runs"] == 5, get("proto", TMP))
ok("不同协议 miss", get("other", TMP) is None)
ok("key 稳定", key("proto") == key("proto"))

print(f"\n==== I26 baseline cache 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
