"""I23：fresh post-GC 内存读数。来自 Unity Profiler API，不是窗口均值。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

CODE = r"""
System.GC.Collect();
System.GC.WaitForPendingFinalizers();
System.GC.Collect();
UnityEngine.Resources.UnloadUnusedAssets();
var mono = UnityEngine.Profiling.Profiler.GetMonoUsedSizeLong();
var total = UnityEngine.Profiling.Profiler.GetTotalAllocatedMemoryLong();
var reserved = UnityEngine.Profiling.Profiler.GetTotalReservedMemoryLong();
var unused = UnityEngine.Profiling.Profiler.GetTotalUnusedReservedMemoryLong();
return "{\"mono\":" + mono + ",\"total\":" + total +
       ",\"reserved\":" + reserved + ",\"unused\":" + unused + "}";
"""


def read(project: Path | str, *, timeout: int = 120) -> dict:
    p = subprocess.run(
        ["unity", "command", "script eval", "--code", CODE,
         "--project-path", str(Path(project).resolve()), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-400:] or p.stdout[-400:])
    d = json.loads(p.stdout)
    result = ((d.get("data") or {}).get("result"))
    if isinstance(result, dict) and isinstance(result.get("result"), str):
        result = json.loads(result["result"])
    elif isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise RuntimeError(f"fresh memory 返回异常: {result!r}")
    return {k: int(result[k]) for k in ("mono", "total", "reserved", "unused")}


def read_median(project: Path | str, n: int = 3) -> dict:
    import statistics
    vals = [read(project) for _ in range(max(1, n))]
    return {k: int(statistics.median(v[k] for v in vals))
            for k in ("mono", "total", "reserved", "unused")}
