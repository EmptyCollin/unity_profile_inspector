"""用生产探针测一个病例的 GC breach；不依赖 Editor 内联 GC API。"""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--gcthr", type=int, default=4096)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    inst = P.install("sim/project", timeout_s=120)
    if not inst.get("ok"):
        print(json.dumps({"ok": False, "install": inst}, ensure_ascii=False, indent=2))
        return 1
    P.wait_alive(8)
    P.clear()
    P.send_cmd(f"gcthr:{a.gcthr}", wait=True, timeout_s=3)
    P.sample(True, 120)
    code = ('var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
            'if(g==null) return "no-game";'
            'g.DebugLoadConfig("{\\"seed\\":20261112,\\"autoplay\\":true,\\"round_seconds\\":30}");'
            'g.DebugStartRun(); return "ok";')
    subprocess.run(["unity", "command", "script eval", "--project-path", "sim/project",
                    "--code", code, "--format", "json"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
    time.sleep(max(1.0, a.seconds))
    P.sample(False)
    ev = []
    if EVENTS.exists():
        ev = [json.loads(x) for x in EVENTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    breaches = [e for e in ev if e.get("kind") == "breach"]
    samples = [e for e in ev if e.get("kind") == "sample"]
    gc_ring = [v for e in breaches if e.get("metric") == "gc_alloc_bytes"
               for v in (e.get("ring_before") or [])]
    gc_ring_sorted = sorted(gc_ring)
    gc_median = (gc_ring_sorted[len(gc_ring_sorted) // 2] if gc_ring_sorted else 0)
    gc_p95 = (gc_ring_sorted[int(len(gc_ring_sorted) * 0.95)] if gc_ring_sorted else 0)
    report = {
        "ok": True, "gcthr": a.gcthr, "seconds": a.seconds,
        "events": len(ev),
        "breach": len(breaches),
        "sample": len(samples),
        "breach_metrics": dict(Counter(str(e.get("metric")) for e in breaches)),
        "sample_metrics": dict(Counter(str(e.get("metric")) for e in samples)),
        "gc_ring_n": len(gc_ring),
        "gc_median": gc_median,
        "gc_p95": gc_p95,
        "gc_max": (max(gc_ring) if gc_ring else 0),
    }
    out = Path(a.out) if a.out else (ROOT / "runs" / "probe_case.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "out": str(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
