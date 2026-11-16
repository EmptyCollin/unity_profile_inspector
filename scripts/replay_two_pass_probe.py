"""回放两轮探针实验：浅采判异常，deep 第二轮定因，先验复现。"""
from __future__ import annotations

import json
import statistics
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


def run_mode(mode: str, replay: str) -> dict:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"mode": mode, "frames": 60,
                               "budget_ms": 12000}), encoding="utf-8")
    OUT.unlink(missing_ok=True)
    prof_off()
    d = unity("script.eval_file", "--file", str(PROBE),
              "--project-path", "test_project", timeout=120)
    if not d.get("success"):
        return {"mode": mode, "ok": False, "error": d.get("errors")}
    spec = load_replay(ROOT / replay)
    ex = ReplayExecutor(Path("test_project").resolve())
    for cmd in spec.commands:
        ex._one(cmd)  # noqa: SLF001
    deadline = time.time() + 90
    while time.time() < deadline and not OUT.exists():
        time.sleep(0.5)
    if not OUT.exists():
        return {"mode": mode, "ok": False, "error": "missing output"}
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    doc["ok"] = True
    return doc


def summary(doc: dict) -> dict:
    def vals(name):
        return [x.get("counters", {}).get(name) for x in (doc.get("counters") or [])
                if name in (x.get("counters") or {})]
    fvals = vals("CPU Main Thread Frame Time")
    gvals = vals("GC Allocated In Frame")
    fnvals = [x.get("total_ms", 0) for x in (doc.get("functions") or [])]
    med = statistics.median(fvals) if fvals else 0
    return {"mode": doc.get("mode"), "frames": len(fvals),
            "frame_median_ns": med, "frame_max_ns": max(fvals or [0]),
            "gc_max": max(gvals or [0]),
            "function_max_ms": max(fnvals or [0]),
            "counter_anomaly": bool(fvals and max(fvals) > max(2 * med, 1)),
            "function_anomaly": bool(fnvals and max(fnvals) > 2.0)}


def main() -> int:
    M.ensure_ready(Path("test_project").resolve())
    sh = run_mode("shallow", "sim/replay/c01.json")
    if not sh.get("ok"):
        print(json.dumps(sh, ensure_ascii=False)); return 1
    shs = summary(sh)
    print("shallow", shs)
    if not (shs["counter_anomaly"] or shs["function_anomaly"]):
        print("no anomaly; stop")
        (ROOT/"runs/replay_two_pass.json").write_text(
            json.dumps({"shallow": shs, "deep": None,
                        "reproduced": False, "reason": "no_anomaly"},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    # 退出 Play，再起 deep
    unity("editor.playmode", "stop", "--project-path", "test_project")
    time.sleep(2)
    M.ensure_ready(Path("test_project").resolve())
    dp = run_mode("deep", "sim/replay/c01.json")
    if not dp.get("ok"):
        print(json.dumps(dp, ensure_ascii=False)); return 1
    dps = summary(dp)
    print("deep", dps)
    reproduced = dps["counter_anomaly"] or dps["function_anomaly"]
    out = {"shallow": shs, "deep": dps, "reproduced": reproduced,
           "deep_only_for_attribution": True}
    (ROOT/"runs/replay_two_pass.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
