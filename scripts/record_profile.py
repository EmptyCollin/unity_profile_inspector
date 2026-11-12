"""录制 Unity Profiler .data（常驻/回归分析用）。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unity(*args):
    p = subprocess.run(["unity", "command", *args, "--project-path", "sim/project",
                        "--format", "json"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"raw": p.stdout[-800:], "returncode": p.returncode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--out", default="runs/profile.data")
    a = ap.parse_args()
    out = (ROOT / a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    r = unity("profiler_record", "--frames", str(a.frames),
              "--output-path", str(out), "--timeout-seconds", str(a.timeout),
              "--deep-profile", "true")
    started = ((r.get("data") or {}).get("result") or {})
    if started.get("status") not in {"queued", "recording", "completed"}:
        print(json.dumps({"started": started, "error": r.get("errors")},
                         ensure_ascii=False, indent=2)[:1200])
        return 1
    print(json.dumps({"start": r.get("success"), "status": started},
                     ensure_ascii=False)[:800])
    deadline = time.time() + a.timeout + 15
    status = None
    while time.time() < deadline:
        s = unity("profiler_record_status")
        status = ((s.get("data") or {}).get("result") or {})
        if status.get("status") in {"completed", "failed", "interrupted"}:
            break
        time.sleep(1)
    print(json.dumps({"status": status, "out": str(out),
                      "exists": out.exists(),
                      "bytes": out.stat().st_size if out.exists() else 0},
                     ensure_ascii=False, indent=2))
    return 0 if status and status.get("status") == "completed" and out.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
