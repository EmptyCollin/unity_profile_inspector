"""I22：live bug/identifier relocation 实验。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def capture(project: Path, replay: Path, tag: str) -> list[str]:
    run_id = f"reloc_{tag}"
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
        raise RuntimeError(p.stderr[-500:])
    mp = ROOT / "runs" / run_id / "merged_candidates.json"
    data = json.loads(mp.read_text(encoding="utf-8"))
    return [str(x.get("root_cause")) for x in data]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--file", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--replay", default=str(ROOT / "runs/i8_lab/paper_replay.json"))
    a = ap.parse_args()
    project = Path(a.project).resolve()
    fp = project / a.file
    original = fp.read_text(encoding="utf-8")
    changed, n = re.subn(rf"\b{re.escape(a.old)}\b", a.new, original)
    if n == 0:
        print("没有命中标识符", file=sys.stderr)
        return 1
    try:
        fp.write_text(changed, encoding="utf-8")
        subprocess.run(["unity", "command", "recompile", "--project-path",
                        str(project), "--format", "json"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
        # 重新进一局，避免旧 Play 局面 NRE
        subprocess.run(["unity", "command", "editor.playmode", "stop",
                        "--project-path", str(project), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
        import time as _t
        _t.sleep(1)
        subprocess.run(["unity", "command", "editor.playmode", "start",
                        "--project-path", str(project), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
        _t.sleep(5)
        roots = capture(project, Path(a.replay), "reloc")
    finally:
        fp.write_text(original, encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "checkout", "--", a.file],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    print(json.dumps({"file": a.file, "old": a.old, "new": a.new,
                      "replaced": n, "roots": roots},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
