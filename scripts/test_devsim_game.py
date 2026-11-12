"""Chroma Rogue 确定性玩法自测（Play Mode + Unity Pipeline script run）。

- 只驱动游戏自身；不读 oracle、不读期望机会。
- 结果写入 runs/devsim_selftest_<ts>.json，并打印每项。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "sim" / "project"
SELFTEST = ROOT / "probe" / "devsim_selftest.cs"
OUT = ROOT / "runs"

CASE = {
    "case_id": "selftest",
    "seed": 12345,
    "autoplay": False,
    "round_seconds": 30.0,
    "round_seconds_step": 0.0,
    "round_seconds_min": 5.0,
    "target_base": 600,
    "target_step": 300,
    "board_start": 10,
    "board_min": 6,
    "board_step_rounds": 2,
    "preview_seconds": 0.0,
    "shop_every": 3,
    "shop_refresh_cost": 40,
    "stack_chance_base": 0.0,
    "stack_chance_per_tier": 0.0,
}


def unity(*args: str, timeout: int = 120) -> dict:
    cmd = ["unity", *args, "--project-path", str(PROJECT), "--format", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"success": False, "stdout": (p.stdout or "")[-800:],
                "stderr": (p.stderr or "")[-800:]}


def main() -> int:
    tmp = Path(tempfile.gettempdir()) / "devsim_case.json"
    tmp.write_text(json.dumps(CASE, ensure_ascii=False), encoding="utf-8")
    try:
        unity("command", "editor.playmode stop")
        r = unity("command", "scene open", "--path", "Assets/Scenes/00_Bootstrap.unity")
        if not r.get("success"):
            print("scene open 失败:", r.get("errors") or r)
            return 1
        r = unity("command", "editor.playmode start")
        if not r.get("success"):
            print("playmode start 失败:", r.get("errors") or r)
            return 1
        time.sleep(2.0)

        r = unity("command", "script run", "--file", str(SELFTEST),
                  "--entry", "DevSimSelfTest.RunAll", "--timeout", "90", timeout=180)
        result = ((r.get("data") or {}).get("result") or {})
        raw = result.get("result")
        if not r.get("success") or not isinstance(raw, str):
            print("script run 失败:", json.dumps(r, ensure_ascii=False)[-1500:])
            return 1
        report = json.loads(raw)
        ts = time.strftime("%Y%m%d_%H%M%S")
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"devsim_selftest_{ts}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for c in report.get("checks", []):
            mark = "ok" if c.get("ok") else "FAIL"
            print(f"  [{mark}] {c.get('name')}"
                  + ("" if c.get("ok") else f" -> {c.get('detail')}"))
        print(f"\n==== devsim 玩法自测: {report.get('passed')}/{len(report.get('checks', []))}"
              f" 通过；证据 {path}")
        return 0 if report.get("ok") else 1
    finally:
        unity("command", "editor.playmode stop")
        try:
            tmp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
