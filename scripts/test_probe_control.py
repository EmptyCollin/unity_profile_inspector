"""I3b：统一探针控制（离线，不碰真编辑器）。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E                       # noqa: E402
from upi.tooling import probe_control as PC            # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_probe_ctl_"))
E.isolate(TMP / "runs")
PROBE = E.RUNS / "_probe"
PROBE.mkdir(parents=True, exist_ok=True)


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# ── send_cmd（不等待）──
PC.send_cmd("status", wait=False)
ok("send_cmd 写 probe_cmd", (PROBE / "probe_cmd.txt").read_text(encoding="utf-8").strip() == "status")
ok("send_cmd 不清空结果", not (PROBE / "probe_res.txt").exists())

# ── legacy_stop 写两个旧通道 ──
PC.legacy_stop()
ok("旧探针卸载命令", (PROBE / "pb_cmd.txt").read_text(encoding="utf-8").strip() == "unload")
ok("旧 sampler 停止命令", (PROBE / "cmd.txt").read_text(encoding="utf-8").strip() == "stop")

# ── install：假 CLI，检查参数与结果 ──
calls = []


class _R:
    success = True
    data = {"result": {"success": True, "result": "installed recorders=12/13"}}
    errors = []


def fake_run(argv, timeout_s=60):
    calls.append((argv, timeout_s))
    return _R()


PC.run_unity = fake_run
res = PC.install(project=TMP / "proj", timeout_s=99)
ok("install 成功", res["ok"] and "installed" in res["detail"])
ok("install 走 script eval_file",
   calls and calls[0][0][:2] == ["command", "script eval_file"])
ok("install 带 --file 探针",
   any("--file" in c[0] and str(PC.PROBE_FILE) in c[0] for c in calls))
ok("install 传 project", any(str(TMP / "proj") in c[0] for c in calls))
ok("install 先卸旧探针", (PROBE / "pb_cmd.txt").read_text(encoding="utf-8").strip() == "unload")

# ── 失败不冒充成功 ──
class _Bad:
    success = False
    data = {"result": {"success": False, "result": "install_error boom"}}
    errors = []


PC.run_unity = lambda argv, timeout_s=60: _Bad()
bad = PC.install(project=TMP / "proj")
ok("install 失败如实报错", (not bad["ok"]) and "install_error" in bad["detail"])

# ── sample / unload：假回执 ──
seen = []
PC.send_cmd = lambda cmd, wait=True, timeout_s=3.0: (seen.append(cmd) or
                                                    ("sample=on window=60" if cmd.startswith("sample:on")
                                                     else ("sample=off" if cmd == "sample:off"
                                                           else "unloaded")))
sp = PC.sample(True, 60)
ok("sample on 发命令", sp["ok"] and seen[-1] == "sample:on:60")
ok("sample off 发命令", PC.sample(False)["ok"] and seen[-1] == "sample:off")
ok("unload 发命令", PC.unload()["ok"] and seen[-1] == "unload")

# ── status 读心跳/状态 ──
(PROBE / "pb_hb.txt").write_text(str(time.time()), encoding="utf-8")
(PROBE / "probe_status.json").write_text('{"state":"ok","line":"sample=off"}', encoding="utf-8")
st = PC.status()
ok("status alive", st["state"] == "alive" and st["heartbeat_age_s"] is not None)
ok("status 带 probe 状态", st["status"].get("line") == "sample=off")
(PROBE / "pb_hb.txt").write_text("old", encoding="utf-8")
os.utime(PROBE / "pb_hb.txt", (time.time() - 100, time.time() - 100))
ok("status 心跳超龄", PC.status()["state"] == "stale")

print()
if FAIL:
    print(f"==== 探针控制测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== 探针控制测试: {len(PASS)} 通过, 0 失败")
