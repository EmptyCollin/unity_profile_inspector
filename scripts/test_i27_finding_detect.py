"""I27 finding_detect：从 action_windows 直接产 per-window Finding。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.finding_detect import findings_from_capture  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def write_run(root: Path, name: str, values: dict[str, float]):
    d = root / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (action, val) in enumerate(values.items()):
        rows.append({
            "window_id": f"cmd-{i}-{action}",
            "frame_start": i * 10, "frame_end": i * 10 + 5,
            "counters": {
                "CPU Main Thread Frame Time": {
                    "median": val, "max": val, "n": 3,
                    "series": [val, val, val],
                },
            },
        })
    (d / "action_windows.json").write_text(json.dumps(rows), encoding="utf-8")
    return d


TMP = Path(tempfile.mkdtemp())
base_dirs = [write_run(TMP, f"base{i}", {"start_game": 1_000_000,
                                         "set_board": 1_000_000,
                                         "wait": 1_000_000})
             for i in range(3)]
cur = write_run(TMP, "cur", {"start_game": 1_050_000,
                             "set_board": 2_000_000,
                             "wait": 1_000_000})

fs = findings_from_capture(cur, baseline_dirs=base_dirs, project_id="p")
by_win = {f.window.window_id: f for f in fs}
ok("只报 set_board 异常", set(by_win) == {"cmd-1-set_board"}, list(by_win))
f = by_win.get("cmd-1-set_board")
ok("finding 窗口精确", f and f.window.index == 1 and f.window.action == "set_board", f)
ok("finding 有量级", f and f.magnitude > 900_000, f)
ok("finding 有参考", f and f.reference["source"] == "baseline_median", f)
ok("finding 无根因字段", f and "root_cause" not in f.model_dump(), f)
ok("finding 证据非空", f and f.evidence_refs, f)

fs2 = findings_from_capture(cur, project_id="p")
ok("无 baseline 时用窗口中位数", any(x.window.window_id == "cmd-1-set_board"
                                     for x in fs2), fs2)

bad = write_run(TMP, "bad", {"start_game": 1_000_000,
                             "set_board": 1_000_000,
                             "wait": 1_000_000})
ok("正常窗口不报", findings_from_capture(
    bad, baseline_dirs=base_dirs, project_id="p") == [])

print(f"\n==== I27 finding_detect: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
