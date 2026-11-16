"""一次性实验：单探针 shallow vs deep，同帧记录 counters/functions。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from capability_run import ReplayExecutor, load_replay  # noqa: E402
import exam_multi as M  # noqa: E402

PROBE = ROOT / "probe" / "upi_unified_probe.cs"
CFG = ROOT / "runs" / "_probe" / "unified_cfg.json"
OUT = ROOT / "runs" / "_probe" / "unified_probe.json"


def unity(*args, timeout=180):
    p = subprocess.run(["unity", "command", *args, "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"success": False, "raw": (p.stdout or p.stderr)[-300:]}


def prof_off():
    code = ("UnityEngine.Profiling.Profiler.enableAllocationCallstacks=false;"
            "UnityEditorInternal.ProfilerDriver.deepProfiling=false;"
            "UnityEditorInternal.ProfilerDriver.enabled=false;"
            "UnityEngine.Profiling.Profiler.enabled=false;"
            "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return 'off';")
    unity("script.eval", "--code", code, "--project-path", "test_project")


def run_mode(mode: str) -> dict:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"mode": mode, "frames": 30,
                               "budget_ms": 6000}), encoding="utf-8")
    OUT.unlink(missing_ok=True)
    prof_off()
    d = unity("script.eval_file", "--file", str(PROBE),
              "--project-path", "test_project", timeout=120)
    if not d.get("success"):
        return {"mode": mode, "ok": False, "error": d.get("errors")}
    spec = load_replay(ROOT / "sim" / "replay" / "c01.json")
    from capability_run import ReplayExecutor
    ex = ReplayExecutor(Path("test_project").resolve())
    for cmd in spec.commands:
        ex._one(cmd)  # noqa: SLF001
    deadline = time.time() + 60
    while time.time() < deadline and not OUT.exists():
        time.sleep(0.5)
    if not OUT.exists():
        return {"mode": mode, "ok": False, "error": "probe output missing"}
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    ctr = doc.get("counters") or []
    fns = doc.get("functions") or []
    c_frames = {int(x.get("frame_idx")) for x in ctr}
    f_frames = {int(x.get("frame_idx")) for x in fns}
    return {"mode": mode, "ok": True, "start_frame": doc.get("start_frame"),
            "last_frame": doc.get("last_frame"),
            "counter_frames": len(c_frames), "function_frames": len(f_frames),
            "common_frames": len(c_frames & f_frames),
            "counter_spikes": max([v for x in ctr for v in (x.get("counters") or {}).values()] or [0]),
            "function_max_ms": max([x.get("total_ms", 0) for x in fns] or [0]),
            "counters": ctr, "functions": fns}


def main() -> int:
    M.ensure_ready(Path("test_project").resolve())
    out = {"shallow": run_mode("shallow"), "deep": run_mode("deep")}
    prof_off()
    (ROOT / "runs" / "unified_experiment.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in out.items():
        print(k, {x: v.get(x) for x in
                  ("ok", "start_frame", "last_frame", "counter_frames",
                   "function_frames", "common_frames", "function_max_ms")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
