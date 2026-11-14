"""I23：配置反事实实验（默认需显式批准）。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import asset_verify as AV  # noqa: E402
from upi.core.config_edit import ConfigEdit, apply, rollback, validate  # noqa: E402
from upi.core.target_metrics import metric_series, stats  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--path", required=True)
    ap.add_argument("--field", required=True)
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--section", default="")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--metric", default="frame_time")
    ap.add_argument("--replay", default=str(ROOT / "runs/i8_lab/replay_3case.json"))
    ap.add_argument("--perception-approved", action="store_true")
    a = ap.parse_args()
    if not a.perception_approved:
        print(json.dumps({"ok": False, "error": "perception_not_approved"},
                         ensure_ascii=False))
        return 2
    project = Path(a.project).resolve()
    edit = ConfigEdit(a.path, a.field, a.before, a.after,
                      section=a.section, index=a.index)
    errs = validate(project, [edit])
    if errs:
        print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
        return 1
    b, _ = AV._capture(project, Path(a.replay), "cfg_before")
    done = apply(project, [edit])
    try:
        subprocess.run(["unity", "command", "script eval", "--code",
                        "UnityEditor.AssetDatabase.Refresh();",
                        "--project-path", str(project), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
        import exam_multi as X
        X.ensure_ready(project)
        af, _ = AV._capture(project, Path(a.replay), "cfg_after")
        bm = stats(metric_series(b, a.metric))
        am = stats(metric_series(af, a.metric))
        delta = (bm["median"] - am["median"]) if bm and am else None
        reliable = bool(
            bm and am and bm["median"]
            and bm["sigma"] <= 0.2 * abs(bm["median"])
            and am["sigma"] <= 0.2 * abs(am["median"]))
        threshold = None
        if reliable:
            threshold = max(0.05 * abs(bm["median"]),
                            3 * max(bm["sigma"], am["sigma"]))
        good = bool(reliable and delta is not None and delta > threshold)
        if not good:
            rollback(project, done)
        print(json.dumps({"ok": good, "metric": a.metric,
                          "before": bm, "after": am, "delta": delta,
                          "reliable": reliable, "threshold": threshold,
                          "rolled_back": not good}, ensure_ascii=False,
                         indent=1))
        return 0 if good else 1
    except Exception as e:  # noqa: BLE001
        rollback(project, done)
        print(json.dumps({"ok": False, "error": str(e)[:300]},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
