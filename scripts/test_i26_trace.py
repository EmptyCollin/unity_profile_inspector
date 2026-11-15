"""I26 P1：离线 trace / window 指标。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.trace_analysis import build_trace  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
cap = TMP / "cap"
cap.mkdir()
(cap / "pass1_timeline.json").write_text(json.dumps([
    {"cmd_index": 0, "type": "set_board", "frame": 100},
    {"cmd_index": 1, "type": "wait", "frame": 101},
    {"cmd_index": 2, "type": "open_shop", "frame": 102},
]), encoding="utf-8")
d = TMP / "cap_a2"
d.mkdir()
(d / "counters.json").write_text(json.dumps({"counters": {
    "CPU Main Thread Frame Time": {"series": [100, 200, 300]},
    "GC Allocated In Frame": {"series": [10, 20, 30]},
}}), encoding="utf-8")
(cap / "merged_candidates.json").write_text(json.dumps([
    {"root_cause": "hot_function:X.Update"},
]), encoding="utf-8")

doc = build_trace("cap", root=TMP)
ok("timeline 读入", len(doc["timeline"]) == 3)
ok("window 读入", doc["n_windows"] == 1 and doc["windows"][0]["index"] == 2)
ok("window action", doc["windows"][0]["action"] == "open_shop",
   doc["windows"][0])
ok("frame_time 中位数", doc["windows"][0]["metrics"]["frame_time"]["median"] == 200,
   doc["windows"][0]["metrics"])
ok("roots 映射", doc["roots"] == ["X.Update"], doc["roots"])

print(f"\n==== I26 trace 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
