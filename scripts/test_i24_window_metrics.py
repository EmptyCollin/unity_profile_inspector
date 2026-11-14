"""I24：action window counters。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.window_metrics import (action_indices, index_for_window,  # noqa: E402
                                     window_counters)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
runs = TMP / "runs"
for idx, val in ((2, 10), (10, 20)):
    d = runs / f"cap_a{idx}"
    d.mkdir(parents=True)
    (d / "counters.json").write_text(json.dumps({"value": val}), encoding="utf-8")
w = window_counters("cap", root=TMP)
ok("读到 a2/a10", sorted(w) == [2, 10], sorted(w))
ok("a10 值正确", w[10]["value"] == 20, w)

replay = TMP / "r.json"
replay.write_text(json.dumps({"commands": [
    {"type": "set_board"}, {"type": "wait"}, {"type": "resolve"},
    {"type": "open_shop"}, {"type": "open_shop"}]}), encoding="utf-8")
idx = action_indices(replay)
ok("动作索引跳过 wait", 1 not in idx and idx[4] == "open_shop", idx)
ok("window 取最后出现", index_for_window(idx, "open_shop") == 4, idx)

print(f"\n==== I24 window counters 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
