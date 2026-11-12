"""帧号对齐实验：Time.frameCount vs ProfilerDriver 帧号的偏移是否稳定。

用法：python scripts/i8_frame_align.py
前置：Unity 在 Play Mode 且游戏已启动（本脚本会自己确保 Play Mode / 启动游戏）。
结果：runs/frame_align_<ts>.json（含每一帧的原始配对）
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "sim" / "project"
PROBE = ROOT / "probe" / "upi_frame_align.cs"
OUT = ROOT / "runs" / "_probe" / "frame_align.json"


def unity(*args, timeout=120):
    p = subprocess.run(["unity", "command", *args, "--project-path", str(PROJECT),
                        "--format", "json"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": (p.stdout or p.stderr)[-300:]}


def status():
    d = unity("editor status")
    s = ((d.get("data") or {}).get("result") or {}) if d.get("success") else {}
    return s


def ensure_play():
    s = status()
    if not s:
        raise RuntimeError("Unity pipeline 不可达")
    if s.get("playMode") != "playing":
        unity("editor.playmode start")
        for _ in range(40):
            time.sleep(0.5)
            if status().get("playMode") == "playing":
                break
    unity("editor set_auto_tick", "--enabled", "true", "--interval-ms", "16",
          "--persist", "false")


def start_game():
    code = ('var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
            'if(g==null) return "no-game";'
            'g.DebugLoadConfig("{\\"seed\\":20261112,\\"autoplay\\":true,'
            '\\"round_seconds\\":300}"); g.DebugStartRun(); return "ok";')
    return unity("script eval", "--code", code, "--timeout", "30")


def main() -> int:
    try:
        ensure_play()
        start_game()
        time.sleep(0.5)
        try:
            OUT.unlink()
        except OSError:
            pass
        d = unity("script eval", "--file", str(PROBE), "--timeout", "60")
        if not d.get("success"):
            raise RuntimeError("align eval 失败：" + json.dumps(d.get("errors"),
                                                               ensure_ascii=False)[:300])
        deadline = time.time() + 60
        while time.time() < deadline and not OUT.exists():
            time.sleep(0.3)
        if not OUT.exists():
            raise RuntimeError("frame_align.json 未落盘")
        rows = json.loads(OUT.read_text(encoding="utf-8"))["rows"]
    finally:
        # 收尾：关 profiler，避免残留
        unity("script eval", "--code",
              "UnityEditorInternal.ProfilerDriver.deepProfiling=false;"
              "UnityEditorInternal.ProfilerDriver.enabled=false;"
              "UnityEngine.Profiling.Profiler.enabled=false;"
              "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return \"off\";",
              "--timeout", "20")

    deltas = [r["profiler_last"] - r["game_frame"] for r in rows]
    first, last = deltas[0], deltas[-1]
    uniq = sorted(set(deltas))
    mono = all(rows[i]["profiler_last"] <= rows[i + 1]["profiler_last"]
               for i in range(len(rows) - 1))
    report = {
        "rows": len(rows),
        "delta_first": first,
        "delta_last": last,
        "delta_min": min(deltas),
        "delta_max": max(deltas),
        "delta_median": statistics.median(deltas),
        "unique_deltas": uniq[:12],
        "unique_delta_count": len(uniq),
        "profiler_monotonic": mono,
        "stable": len(uniq) <= 2,
        "samples": rows[:3] + rows[-3:],
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = ROOT / "runs" / f"frame_align_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rows={report['rows']} delta first={first} last={last} "
          f"min={report['delta_min']} max={report['delta_max']} "
          f"median={report['delta_median']} unique={report['unique_delta_count']}")
    print(f"monotonic={mono} stable={report['stable']}")
    print(f"EVIDENCE {p}")
    return 0 if report["stable"] and mono else 1


if __name__ == "__main__":
    raise SystemExit(main())
