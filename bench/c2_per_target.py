"""I24 P2：逐 target 跑单补丁反事实。"""
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
    ap.add_argument("--targets", required=True,
                    help="逗号分隔的 target 完整 mechanism")
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--replay", default=str(ROOT / "runs/i8_lab/replay_3case.json"))
    ap.add_argument("--baselines", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "runs/_probe/c2_per_target.json"))
    a = ap.parse_args()
    results = []
    for target in [x.strip() for x in a.targets.split(",") if x.strip()]:
        t0 = time.time()
        p = subprocess.run(
            [sys.executable, str(ROOT / "bench" / "counterfactual.py"),
             "--project", a.project, "--project-id", a.project_id,
             "--replay", a.replay, "--target", target,
             "--baselines", str(a.baselines)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1800)
        try:
            doc = json.loads(p.stdout)
        except Exception:  # noqa: BLE001
            doc = {"ok": False, "raw": p.stdout[-500:]}
        doc["elapsed_total"] = round(time.time() - t0, 1)
        doc["target"] = target
        results.append(doc)
        print(target, doc.get("run_id"), doc.get("records"), flush=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
