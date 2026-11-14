"""I24：单补丁反事实入口。一次只跑一个 target。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--replay", default=str(ROOT / "runs/i8_lab/replay_3case.json"))
    ap.add_argument("--target", required=True)
    ap.add_argument("--baselines", type=int, default=3)
    ap.add_argument("--plan-repeat", type=int, default=3)
    a = ap.parse_args()
    started = time.time()
    cmd = [sys.executable, str(ROOT / "scripts" / "exam_batch.py"),
           "--project", a.project, "--project-id", a.project_id,
           "--replay", a.replay, "--baselines", str(a.baselines),
           "--rounds", "1", "--targets", a.target, "--max-units", "1",
           "--plan-repeat", str(a.plan_repeat)]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    runs = sorted((ROOT / "runs").glob(f"{a.project_id}_*"),
                  key=lambda x: x.stat().st_mtime)
    with_cf = [r for r in runs if (r / "counterfactual.jsonl").exists()]
    latest = with_cf[-1] if with_cf else (runs[-1] if runs else None)
    records = []
    if latest and (latest / "counterfactual.jsonl").exists():
        for line in (latest / "counterfactual.jsonl").read_text(
                encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    result = {
        "ok": p.returncode == 0,
        "target": a.target,
        "run_id": latest.name if latest else "",
        "records": records,
        "elapsed": round(time.time() - started, 1),
        "stderr_tail": (p.stderr or "")[-500:],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if p.returncode == 0 and records else 1


if __name__ == "__main__":
    raise SystemExit(main())
