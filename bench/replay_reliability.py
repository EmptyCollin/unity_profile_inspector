"""I24：压缩 replay 可靠性。fullwait vs shortwait，各 N 次。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def one(project: Path, replay: Path, tag: str) -> dict:
    run_id = f"rel_{tag}_{time.strftime('%H%M%S')}"
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "capability_run.py"),
         "--project", str(project), "--project-id", project.name,
         "--replay", str(replay), "--no-save", "--run-id", run_id,
         "--light", "--target-windows"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600, env=dict(os.environ))
    elapsed = round(time.time() - t0, 1)
    if p.returncode != 0:
        return {"ok": False, "elapsed": elapsed, "error": p.stderr[-300:]}
    mp = ROOT / "runs" / run_id / "merged_candidates.json"
    cands = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else []
    roots = sorted({str(x.get("root_cause", "")).split(":", 1)[-1]
                    for x in cands})
    return {"ok": True, "elapsed": elapsed, "roots": roots,
            "count": len(cands)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--full", default=str(ROOT / "runs/i8_lab/paper_replay_fullwait.json"))
    ap.add_argument("--short", default=str(ROOT / "runs/i8_lab/paper_replay_shortwait.json"))
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--out", default=str(ROOT / "runs/_probe/replay_reliability.json"))
    a = ap.parse_args()
    project = Path(a.project).resolve()
    result = {"project": str(project), "n": a.n}
    for name, path in (("fullwait", Path(a.full)), ("shortwait", Path(a.short))):
        runs = [one(project, path, name) for _ in range(a.n)]
        roots = [set(r.get("roots") or []) for r in runs if r.get("ok")]
        union = set().union(*roots) if roots else set()
        inter = set.intersection(*roots) if roots else set()
        result[name] = {
            "runs": runs,
            "union": sorted(union),
            "always": sorted(inter),
            "missing": sorted(union - inter),
            "counts": [r.get("count") for r in runs],
            "elapsed": [r.get("elapsed") for r in runs],
        }
    f = set(result["fullwait"]["union"])
    s = set(result["shortwait"]["union"])
    result["compare"] = {
        "missing_in_short": sorted(f - s),
        "new_in_short": sorted(s - f),
        "same_union": f == s,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("n", "compare")},
                     ensure_ascii=False, indent=2))
    for name in ("fullwait", "shortwait"):
        print(name, "always", len(result[name]["always"]),
              "missing", result[name]["missing"],
              "counts", result[name]["counts"],
              "elapsed", result[name]["elapsed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
