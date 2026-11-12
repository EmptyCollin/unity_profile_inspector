"""固定匹配次数负载：同一棋盘 1000 次 FindMatches/CountByColor，探针抓 GC 帧。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.tooling import probe_control as P  # noqa: E402

EVENTS = ROOT / "runs" / "_probe" / "probe_events.jsonl"
COLORS = [(i // 6 + i % 6) % 5 for i in range(36)]
LAYERS = [1] * 36


def unity_eval(code: str, timeout: int = 120) -> dict:
    p = subprocess.run(["unity", "command", "script eval", "--project-path", "sim/project",
                        "--code", code, "--format", "json", "--timeout", str(timeout)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"raw": p.stdout[-800:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=1000)
    ap.add_argument("--out", default="runs/probe_match.json")
    a = ap.parse_args()
    inst = P.install("sim/project", timeout_s=120)
    if not inst.get("ok"):
        print(json.dumps({"ok": False, "install": inst}, ensure_ascii=False, indent=2))
        return 1
    P.wait_alive(8)
    P.clear()
    P.send_cmd("gcthr:0", wait=True, timeout_s=3)
    P.sample(True, 120)
    setup = (
        'var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
        'if(g==null) return "no-game";'
        'g.DebugLoadConfig("{\\"seed\\":20261112,\\"autoplay\\":false}");'
        'g.DebugStartRun();'
        'g.DebugSetBoard(6,new int[]{' + ",".join(map(str, COLORS)) + '},'
        'new int[]{' + ",".join(map(str, LAYERS)) + '});'
        'return "ready";')
    print("setup", json.dumps(unity_eval(setup), ensure_ascii=False)[:300])
    time.sleep(1.5)
    loop = (
        'var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
        'var f=g.GetType().GetField("model",System.Reflection.BindingFlags.NonPublic|'
        'System.Reflection.BindingFlags.Instance);'
        'var m=(DevSim.BoardModel)f.GetValue(g);'
        'for(int i=0;i<' + str(a.iterations) + ';i++){var h=m.FindMatches(); m.CountByColor(h);}'
        'return "done";')
    print("loop", json.dumps(unity_eval(loop), ensure_ascii=False)[:300])
    time.sleep(2.0)
    P.sample(False)
    ev = []
    if EVENTS.exists():
        ev = [json.loads(x) for x in EVENTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    series = [v for e in ev if e.get("kind") == "sample"
              and e.get("metric") == "Memory/GcAllocInFrameBytes"
              for v in (e.get("series") or [])]
    s = sorted(series)
    report = {"ok": True, "iterations": a.iterations,
              "sample_n": len(s),
              "gc_median": s[len(s) // 2] if s else 0,
              "gc_p95": s[int(len(s) * 0.95)] if s else 0,
              "gc_max": max(s) if s else 0,
              "events": len(ev),
              "sample_metrics": dict(Counter(str(e.get("metric")) for e in ev
                                             if e.get("kind") == "sample"))}
    out = Path(a.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "out": str(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
