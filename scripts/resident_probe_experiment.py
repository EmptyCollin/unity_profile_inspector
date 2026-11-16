"""resident 触发实验：浅采滚动，触发后开深采并导出前后窗口。"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from capability_run import ReplayExecutor, load_replay  # noqa: E402
import exam_multi as M  # noqa: E402

PROBE = ROOT / "probe" / "upi_unified_probe.cs"
CFG = ROOT / "runs" / "_probe" / "unified_cfg.json"
OUT = ROOT / "runs" / "_probe" / "unified_probe.json"
TRIG = ROOT / "runs" / "_probe" / "unified_trigger.txt"


def unity(*args, timeout=180):
    p = subprocess.run(["unity", "command", *args, "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"success": False, "raw": (p.stdout or p.stderr)[-300:]}


def main() -> int:
    M.ensure_ready(Path("test_project").resolve())
    TRIG.unlink(missing_ok=True)
    CFG.write_text(json.dumps({"mode": "resident", "frames": 60,
                               "budget_ms": 20000, "pre_frames": 8,
                               "post_frames": 8,
                               "trigger_file": str(TRIG)}), encoding="utf-8")
    OUT.unlink(missing_ok=True)
    d = unity("script.eval_file", "--file", str(PROBE),
              "--project-path", "test_project")
    print("install", d.get("success"), d.get("errors"))
    threading.Timer(1.0, lambda: TRIG.write_text("1", encoding="utf-8")).start()
    spec = load_replay(ROOT / "sim" / "replay" / "c01.json")
    ex = ReplayExecutor(Path("test_project").resolve())
    for cmd in spec.commands:
        ex._one(cmd)  # noqa: SLF001
    deadline = time.time() + 60
    while time.time() < deadline and not OUT.exists():
        time.sleep(0.5)
    if not OUT.exists():
        print("no output"); return 1
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    print(json.dumps({k: doc.get(k) for k in
                      ("mode", "start_frame", "trigger_frame", "last_frame",
                       "pre_frames", "post_frames")}, ensure_ascii=False))
    print("counters", len(doc.get("counters") or []),
          "functions", len(doc.get("functions") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
