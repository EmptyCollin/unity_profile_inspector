"""I23：资源编辑的同 replay 验证（默认只在批准后执行）。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_edit import AssetEdit, apply, rollback, validate  # noqa: E402
from upi.core.fresh_memory import read_median  # noqa: E402
from upi.core.target_metrics import metric_series, stats  # noqa: E402


def _capture(project: Path, replay: Path, tag: str) -> tuple[dict, str]:
    run_id = f"asset_{tag}_{time.strftime('%H%M%S')}"
    cmd = [sys.executable, str(ROOT / "scripts" / "capability_run.py"),
           "--project", str(project), "--project-id", project.name,
           "--replay", str(replay), "--no-save", "--run-id", run_id,
           "--light", "--target-windows"]
    env = dict(os.environ)
    env.setdefault("UPI_GAME_TYPE", "DevSim.ChromaGame")
    env.setdefault("UPI_BOARD_TYPE", "DevSim.BoardModel")
    started = time.time()
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600,
                       env=env)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-500:])
    # 合并该次 capture 期间新写的 counters
    merged: dict = {}
    for cp in (ROOT / "runs").glob("*/counters.json"):
        try:
            if cp.stat().st_mtime < started:
                continue
            d = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for name, node in (d.get("counters") or {}).items():
            if isinstance(node, dict) and isinstance(node.get("series"), list):
                merged.setdefault(name, {"series": []})["series"].extend(
                    float(x) for x in node["series"])
    return {"counters": merged}, run_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--asset", required=True)
    ap.add_argument("--field", default="maxTextureSize")
    ap.add_argument("--before", default="2048")
    ap.add_argument("--after", default="1024")
    ap.add_argument("--metric", default="memory")
    ap.add_argument("--replay", default=str(ROOT / "runs/i8_lab/replay_3case.json"))
    ap.add_argument("--perception-approved", action="store_true",
                    help="资源视觉变化必须显式批准")
    a = ap.parse_args()
    if not a.perception_approved:
        print(json.dumps({"ok": False, "error": "perception_not_approved"},
                         ensure_ascii=False))
        return 2
    project = Path(a.project).resolve()
    before = int(a.before) if a.before.isdigit() else a.before
    after = int(a.after) if a.after.isdigit() else a.after
    edit = AssetEdit(a.asset, a.field, before, after)
    errs = validate(project, [edit])
    if errs:
        print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
        return 1
    b, _ = _capture(project, Path(a.replay), "before")
    bm_fresh = read_median(project) if a.metric == "memory" else None
    done = apply(project, [edit])
    try:
        subprocess.run(["unity", "command", "script eval", "--code",
                        f"UnityEditor.AssetDatabase.ImportAsset(\"{a.asset}\", "
                        "UnityEditor.ImportAssetOptions.ForceUpdate);",
                        "--project-path", str(project), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=180)
        af, _ = _capture(project, Path(a.replay), "after")
        if a.metric == "memory":
            am_fresh = read_median(project)
            bm = bm_fresh
            am = am_fresh
            delta = (bm["total"] - am["total"]) if bm and am else None
            threshold = max(0.01 * abs(bm["total"]),
                            4 * 1024 * 1024) if bm else None
            reliable = True
        else:
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
