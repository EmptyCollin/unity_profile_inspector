"""I26-ext E3：asset opportunity 分账。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.opportunity import scan  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
r = TMP / "Assets" / "Resources" / "DevSim"
r.mkdir(parents=True)
(r / "big.txt").write_bytes(b"x" * 200_000)
(r / "small.txt").write_bytes(b"x" * 10)
(r / "big.png").write_bytes(b"x" * 200_000)
out = scan(TMP)
ok("大文本命中", len(out) == 1 and out[0].asset_path.endswith("big.txt"), out)
ok("小文件忽略", all("small" not in o.asset_path for o in out))
ok("png 不在此类", all(not o.asset_path.endswith(".png") for o in out))
ok("status manual_review", out[0].status == "manual_review", out[0].as_dict())

print(f"\n==== I26-ext opportunity 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
