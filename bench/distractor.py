"""I22：distractor 实验。插入未调用热函数，runtime 不应报 finding。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def capture(project: Path, replay: Path, tag: str) -> list[str]:
    run_id = f"distractor_{tag}"
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
    ap.add_argument("--file", default="Assets/DevSim/Runtime/ChromaGame.cs")
    ap.add_argument("--method", default="DistractorUnused")
    ap.add_argument("--replay", default=str(ROOT / "runs/i8_lab/paper_replay.json"))
    a = ap.parse_args()
    project = Path(a.project).resolve()
    fp = project / a.file
    original = fp.read_text(encoding="utf-8")
    idx = original.rfind("}")
    injected = (original[:idx] +
                f"\n        private void {a.method}()\n"
                "        {\n"
                "            var t = new Texture2D(2, 2);\n"
                "            t.Apply();\n"
                "        }\n" + original[idx:])
    try:
        fp.write_text(injected, encoding="utf-8")
        subprocess.run(["unity", "command", "recompile", "--project-path",
                        str(project), "--format", "json"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
        subprocess.run(["unity", "command", "editor.playmode", "stop",
                        "--project-path", str(project), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
        time.sleep(1)
        subprocess.run(["unity", "command", "editor.playmode", "start",
                        "--project-path", str(project), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
        time.sleep(5)
        roots = capture(project, Path(a.replay), "distractor")
    finally:
        fp.write_text(original, encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "checkout", "--", a.file],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    hit = [x for x in roots if a.method in x]
    print(json.dumps({"method": a.method, "hit": hit,
                      "roots": roots, "passed": not hit},
                     ensure_ascii=False, indent=2))
    return 0 if not hit else 1


if __name__ == "__main__":
    raise SystemExit(main())
