"""启动 Unity 前先查实例：可达就复用，查不到才打开，绝不盲开第二个。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def call(*args):
    # CLI 只接受“一段命令名”：`editor status` 必须作为单个 argv 传入；
    # 拆成 editor / status 会被当成命令名 editor，报 Command Not Found。
    p = subprocess.run(["unity", *args, "--project-path", "sim/project",
                        "--format", "json"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"raw": (p.stdout or p.stderr)[-500:]}


def status():
    last = None
    for _ in range(3):
        r = call("command", "editor status")
        if r.get("success"):
            s = ((r.get("data") or {}).get("result") or {})
            if s.get("status"):
                return s
        last = r
        time.sleep(0.8)
    return None


def unity_windows() -> list[dict]:
    """任何带窗口的 Unity 都视为已存在；状态查不到也不许再开一个。"""
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process Unity -ErrorAction SilentlyContinue | "
         "Where-Object { $_.MainWindowTitle } | "
         "Select-Object Id,MainWindowTitle | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    raw = (ps.stdout or "").strip()
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else [v]
    except Exception:
        return [{"raw": raw[:200]}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=30.0)
    a = ap.parse_args()
    s = status()
    if s:
        print(json.dumps({"reused": True, "status": s.get("status"),
                          "playMode": s.get("playMode"),
                          "frameCount": s.get("frameCount")}, ensure_ascii=False))
        return 0
    wins = unity_windows()
    if wins:
        print(json.dumps({"reused": False, "opened": False,
                          "existing_unreachable": wins,
                          "message": "Unity window exists but pipeline unreachable; NOT opening another"},
                         ensure_ascii=False))
        return 2
    print(json.dumps({"reused": False, "message": "no Unity window; opening once"},
                     ensure_ascii=False))
    subprocess.Popen(["unity", "open", "sim/project", "--format", "json", "--no-banner"],
                     cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + a.wait
    while time.time() < deadline:
        time.sleep(1)
        s = status()
        if s:
            print(json.dumps({"opened": True, "status": s.get("status")},
                             ensure_ascii=False))
            return 0
    print(json.dumps({"opened": False, "error": "timeout waiting for editor"},
                     ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
