"""Pass 2：只对指定 window 做深采，生成独立 .data。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import capability_run as C  # noqa: E402
import exam_multi as M  # noqa: E402
from upi.core import capture_config as CC  # noqa: E402
from upi.core.retention import cleanup_profiles  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--replay", default="sim/replay/c01.json")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--windows", required=True,
                    help="逗号分隔的 window_id")
    a = ap.parse_args()
    selected = {x.strip() for x in a.windows.split(",") if x.strip()}
    project = Path(a.project).resolve()
    run = ROOT / "runs" / a.run_id
    prof_dir = run / "profiles"
    prof_dir.mkdir(parents=True, exist_ok=True)
    gb = int(CC.load().get("profile_store_gb") or 20)
    cleanup_profiles(ROOT / "runs", days=0, max_bytes=gb * 1024 ** 3)
    M.ensure_ready(project)
    C._eval(project, ("UnityEngine.Profiling.Profiler.maxUsedMemory=256*1024*1024;"
                      "UnityEngine.Profiling.Profiler.enabled=true;"
                      "UnityEditorInternal.ProfilerDriver.enabled=true;"
                      "UnityEditorInternal.ProfilerDriver.profileEditor=true;"
                      "UnityEngine.Profiling.Profiler.enableAllocationCallstacks=true;"
                      "return 1;"), timeout=30)
    spec = C.load_replay(ROOT / a.replay)
    ex = C.ReplayExecutor(project)
    windows = []
    for i, cmd in enumerate(spec.commands):
        tag = f"cmd-{i}-{cmd.type}"
        take = tag in selected
        if take:
            C._eval(project, "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return 1;",
                    timeout=30)
        ex._one(cmd)  # noqa: SLF001
        if take:
            p = prof_dir / f"{tag}.data"
            p.unlink(missing_ok=True)
            info = C.save_profile(project, p)
            info["window_id"] = tag
            info["size"] = p.stat().st_size if p.is_file() else 0
            info["raw_status"] = "ok" if info.get("exists") and info["size"] > 4096 else "raw_missing"
            windows.append(info)
            C._eval(project, "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return 1;",
                    timeout=30)
    (prof_dir / "profile_windows.json").write_text(
        json.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8")
    C._eval(project, "UnityEditorInternal.ProfilerDriver.enabled=false;"
                     "UnityEngine.Profiling.Profiler.enabled=false;return 1;", timeout=30)
    print(json.dumps(windows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
