"""测量缺陷态 vs 修复态的 replay 守护值，判断 breach 阈值是否可分。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config.project_profile import active_profile  # noqa: E402
from upi.agents import fixer  # noqa: E402
from upi.core.regression import guards_from_events  # noqa: E402
from upi.core.replay_driver import UnityReplayDriver  # noqa: E402
from upi.core.regression import ReplaySuite  # noqa: E402
from upi.tooling import probe_control as PC  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402


def git(*a):
    p = subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def main() -> int:
    prof = active_profile()
    fixture = Path(prof.abs_project)
    target = prof.subject_file_abs
    ref = prof.defect_ref(prof.fix_subject)
    dirty, _ = SN.workdir_state(fixture, ROOT)
    if dirty or target is None:
        print("dirty/ref missing")
        return 2
    original = git("rev-parse", "HEAD")
    suite = ReplaySuite(suite_id="measure", project=str(fixture),
                        scene="Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity",
                        protocol_fingerprint="fp-measure", window_s=2)
    drv = UnityReplayDriver(warmup_s=2, window_s=2)
    out = {}
    try:
        target.write_bytes(ref.read_bytes())
        git("add", str(target.relative_to(ROOT)).replace("\\", "/"))
        git("commit", "-m", "TEMP: measure defect")
        cg = fixer.compile_gate(project=fixture, wait_s=120)
        out["defect_compile"] = bool(cg.get("ok"))
        out["defect_probe"] = bool(PC.install().get("ok"))
        PC.send_cmd("thr:2000000")
        _, evs = drv.run_once(suite)
        out["defect"] = guards_from_events(evs)
        out["defect_breaches"] = sum(1 for e in evs if e.kind == "breach")
    finally:
        git("reset", "--hard", original)
    try:
        cg = fixer.compile_gate(project=fixture, wait_s=120)
        out["fixed_compile"] = bool(cg.get("ok"))
        out["fixed_probe"] = bool(PC.install().get("ok"))
        PC.send_cmd("thr:2000000")
        _, evs = drv.run_once(suite)
        out["fixed"] = guards_from_events(evs)
        out["fixed_breaches"] = sum(1 for e in evs if e.kind == "breach")
    finally:
        git("reset", "--hard", original)
    out["head"] = git("rev-parse", "HEAD")
    out["clean"] = not SN.workdir_state(fixture, ROOT)[0]
    p = ROOT / "runs" / f"measure_frame_{time.strftime('%H%M%S')}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("evidence", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
