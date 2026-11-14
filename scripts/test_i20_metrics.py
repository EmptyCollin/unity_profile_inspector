"""I20 metric 读取测试。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.target_metrics import baseline_after  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
b = TMP / "b.json"
a = TMP / "a.json"
b.write_text(json.dumps({"counters": {
    "CPU Main Thread Frame Time": {"series": [50, 51, 49]}}}), encoding="utf-8")
a.write_text(json.dumps({"counters": {
    "CPU Main Thread Frame Time": {"series": [30, 31, 29]}}}), encoding="utf-8")
r = baseline_after(b, a, "frame_time")
ok("baseline median", r["before"]["median"] == 50, r)
ok("delta 计算", r["delta"] == 20, r)
ok("sigma", r["before"]["sigma"] > 0, r)

print(f"\n==== I20 metric 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
