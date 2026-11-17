"""I27 detect 接线：run_dir + baseline dirs → Finding[] → legacy Issue(window)。"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.nodes import make_nodes  # noqa: E402

PASS, FAIL = [], []
BASE = ROOT / "runs"
NAMES = ["_i27_base1", "_i27_base2", "_i27_base3", "_i27_cap", "_i27_run"]


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def write_run(name, values):
    d = BASE / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    rows = []
    for i, val in enumerate(values):
        action = ("start_game", "set_board", "wait")[i]
        rows.append({
            "window_id": f"cmd-{i}-{action}",
            "frame_start": i * 10, "frame_end": i * 10 + 5,
            "counters": {"CPU Main Thread Frame Time": {
                "median": val, "max": val, "n": 3,
                "series": [val, val, val]}},
        })
    (d / "action_windows.json").write_text(json.dumps(rows), encoding="utf-8")
    return d


try:
    for n in ("_i27_base1", "_i27_base2", "_i27_base3"):
        write_run(n, [1_000_000, 1_000_000, 1_000_000])
    cap = write_run("_i27_cap", [1_050_000, 1_000_000, 2_000_000])
    run = BASE / "_i27_run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "baseline_capture.json").write_text(json.dumps(
        {"capture_id": cap.name, "project_id": "p",
         "replay": "runs/i8_lab/paper_replay.json"}), encoding="utf-8")

    nodes = make_nodes(run)
    d = nodes["detect"]({
        "project": str(ROOT), "project_id": "p",
        "baseline_dirs": [str(BASE / n) for n in
                          ("_i27_base1", "_i27_base2", "_i27_base3")],
    })
    ok("detect proceed", d.get("route") == "proceed", d.get("route"))
    ok("产 findings_v2", len(d.get("findings_v2") or []) == 1,
       d.get("findings_v2"))
    pf = d.get("primary_finding") or {}
    ok("primary window 精确", pf.get("window", {}).get("window_id")
       == "cmd-2-wait", pf.get("window"))
    ok("legacy issue window 精确", (d.get("issue") or {}).get("window")
       == "cmd-2-wait", (d.get("issue") or {}).get("window"))
    ok("legacy issue 有 evidence",
       (d.get("issue") or {}).get("evidence", {}).get("source")
       == "action_windows", (d.get("issue") or {}).get("evidence"))
finally:
    for n in NAMES:
        p = BASE / n
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

print(f"\n==== I27 detect wiring: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
