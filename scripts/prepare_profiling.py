"""在进入 Play 之前打开 Profiler/Deep Profile。

Unity 的方法 deep instrumentation 必须在 Play 前启用；进入 Play 后再设
`ProfilerDriver.deepProfiling=true` 只能看到顶层 Update。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="sim/project")
    a = ap.parse_args()
    code = ('UnityEngine.Profiling.Profiler.enabled=true;'
            'UnityEditorInternal.ProfilerDriver.enabled=true;'
            'UnityEditorInternal.ProfilerDriver.deepProfiling=true;'
            'UnityEngine.Application.runInBackground=true;'
            'return "deep="+UnityEditorInternal.ProfilerDriver.deepProfiling;')
    p = subprocess.run(["unity", "command", "script eval", "--project-path", a.project,
                        "--code", code, "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        d = json.loads(p.stdout)
        ok = d.get("success") and (d.get("data") or {}).get("result", {}).get("success")
        print(json.dumps({"ok": bool(ok),
                          "result": (d.get("data") or {}).get("result", {}).get("result"),
                          "errors": d.get("errors")}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    except Exception:
        print(p.stdout[-1000:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
