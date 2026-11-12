"""devsim replay：把外部 scenario 写进临时配置，启动 Unity 播 case 并截图。

oracle 不在这里读取；这里只读 sim/replay/scenarios.json（seed/性能开关）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT = ROOT / "sim" / "project"
DEFAULT_SCENARIOS = ROOT / "sim" / "replay" / "scenarios.json"


def unity(*args: str, timeout: int = 90) -> dict:
    cmd = ["unity", *args, "--format", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"success": False, "stdout": p.stdout[-500:], "stderr": p.stderr[-500:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(DEFAULT_PROJECT))
    ap.add_argument("--case", required=True)
    ap.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    ap.add_argument("--out", default="")
    ap.add_argument("--wait", type=float, default=3.0)
    ns = ap.parse_args()

    project = Path(ns.project).resolve()
    if not (project / "Assets").is_dir():
        print(f"不是 Unity 工程：{project}")
        return 2
    scenarios = json.loads(Path(ns.scenarios).read_text(encoding="utf-8"))
    if ns.case not in scenarios:
        print(f"未知 case：{ns.case}")
        return 2

    tmp = Path(tempfile.gettempdir()) / "devsim_case.json"
    tmp.write_text(json.dumps(scenarios[ns.case], ensure_ascii=False), encoding="utf-8")
    out = Path(ns.out) if ns.out else ROOT / "runs" / f"devsim_{ns.case}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 先确保不在 play mode（上一次交互可能停在游戏里）
        unity("command", "editor.playmode stop", "--project-path", str(project))
        r = unity("command", "scene open", "--path", "Assets/Scenes/00_Bootstrap.unity",
                  "--project-path", str(project))
        if not r.get("success"):
            print("scene open 失败：", r.get("errors") or r)
            return 1
        r = unity("command", "editor.playmode start", "--project-path", str(project))
        if not r.get("success"):
            print("playmode start 失败：", r.get("errors") or r)
            return 1
        time.sleep(max(0.5, ns.wait))
        # 默认场景是 Lobby；点“开始游戏”进入实际游戏（坐标按 1280x720 Game view）
        unity("command", "simulate_pointer", "--x", "253", "--y", "340",
              "--action", "click", "--project-path", str(project))
        time.sleep(1.0)
        r = unity("command", "capture game_view", "--output-path", str(out),
                  "--project-path", str(project))
        print("截图：", (r.get("data") or {}).get("result", {}).get("savedPath") or out)
        return 0
    finally:
        unity("command", "editor.playmode stop", "--project-path", str(project))
        try: tmp.unlink()
        except OSError: pass


if __name__ == "__main__":
    raise SystemExit(main())
