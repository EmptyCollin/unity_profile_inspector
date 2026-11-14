"""I22：反作弊实验。当前实现 action ablation。"""
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


def remove_action(spec: dict, action_type: str, occurrence: int = -1,
                  all_occurrences: bool = False) -> dict:
    cmds = list(spec.get("commands") or [])
    idxs = [i for i, c in enumerate(cmds) if c.get("type") == action_type]
    if not idxs:
        raise ValueError(f"没有 action: {action_type}")
    if all_occurrences:
        for i in sorted(idxs, reverse=True):
            cmds.pop(i)
    else:
        idx = idxs[occurrence]
        cmds.pop(idx)
    out = dict(spec)
    out["commands"] = cmds
    out["notes"] = f"{spec.get('notes','')} | ablate {action_type}"
    return out


def capture(project: Path, replay: Path, tag: str) -> set[str]:
    run_id = f"anti_{tag}_{time.strftime('%H%M%S')}"
    cmd = [sys.executable, str(ROOT / "scripts" / "capability_run.py"),
           "--project", str(project), "--project-id", project.name,
           "--replay", str(replay), "--no-save", "--run-id", run_id,
           "--light", "--target-windows"]
    env = dict(os.environ)
    env.setdefault("UPI_GAME_TYPE", "DevSim.ChromaGame")
    env.setdefault("UPI_BOARD_TYPE", "DevSim.BoardModel")
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600,
                       env=env)
    if p.returncode != 0:
        raise RuntimeError(f"capture failed: {p.stderr[-500:]}")
    mp = ROOT / "runs" / run_id / "merged_candidates.json"
    if not mp.exists():
        return set()
    data = json.loads(mp.read_text(encoding="utf-8"))
    return {str(x.get("root_cause", "")).split(":", 1)[-1] for x in data}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--replay", required=True)
    ap.add_argument("--action", required=True)
    ap.add_argument("--all", action="store_true",
                    help="删除该 action 的全部出现")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    project = Path(a.project).resolve()
    spec = json.loads(Path(a.replay).read_text(encoding="utf-8"))
    ablated = remove_action(spec, a.action, all_occurrences=a.all)
    tmp = ROOT / "runs" / "_probe" / f"ablate_{a.action}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(ablated, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    orig = capture(project, Path(a.replay), "orig")
    abl = capture(project, tmp, "abl")
    result = {
        "project": str(project),
        "action": a.action,
        "original": sorted(orig),
        "ablated": sorted(abl),
        "disappeared": sorted(orig - abl),
        "appeared": sorted(abl - orig),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
