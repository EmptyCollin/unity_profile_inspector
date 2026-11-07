"""scripts/fd_direct_test.py —— 用公开 FrameDebugger.enabled 开关验证当帧事件可取性。

驱动方式说明（关键约束 I9）：eval 临时类型不跨调用存活，所以每一步都
重新注入 fd_direct.cs，并把动作写进状态文件；引擎级状态（enabled 开关）
跨 eval 存活，因此 enable→read 的序列成立。

步骤：enable → read → addcubes → read → disable。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"
PROBE = ROOT / "runs" / "_probe"
SRC = ROOT / "probe" / "fd_direct.cs"


def unity(*args: str, timeout: int = 200) -> dict:
    out = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:300]}]}


def step(action: str) -> str:
    PROBE.mkdir(parents=True, exist_ok=True)
    (PROBE / "fd_direct_state.txt").write_text(action, encoding="utf-8")
    dst = PROJECT / "fd_direct.cs"
    shutil.copy(SRC, dst)
    r = unity("command", "script eval_file", "--file", "fd_direct.cs",
              "--project-path", str(PROJECT), timeout=240)
    dst.unlink(missing_ok=True)
    if r.get("success"):
        return str((r["data"].get("result") or {}).get("result", ""))
    return "ERR " + str(r.get("errors", [{}])[0].get("message", ""))[:400]


def play_state() -> str:
    r = unity("command", "editor status", "--project-path", str(PROJECT))
    res = (r.get("data") or {}).get("result") or {}
    return str(res.get("playMode"))


def main() -> int:
    print("playMode:", play_state())
    if play_state() != "playing":
        print("需要 Play Mode（编辑器渲染只在 Play 时抓帧），跳过不强行进入")
        unity("command", "editor.playmode start", "--project-path", str(PROJECT))
        time.sleep(6)

    print("\n[1] enable（公开开关）")
    out = step("enable")
    print("   ", out[:200])

    print("\n[2] read（当前帧事件）")
    time.sleep(2)
    out = step("read")
    print("   ", out[:800])
    fp = PROBE / "fd_direct.json"
    if fp.exists():
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            print("   解析: events=%s with_obj=%s draw_sum=%s util_count=%s locally_supported=%s"
                  % (d.get("events"), d.get("with_obj"), d.get("draw_sum"),
                     d.get("util_count"), d.get("locally_supported")))
            if d.get("batch_break_causes"):
                print("   合批破坏原因（引擎自述）:", d["batch_break_causes"][:2])
        except json.JSONDecodeError as e:
            print("   JSON 解析失败:", str(e)[:120])

    print("\n[3] addcubes → read（陈旧性对照）")
    print("   ", step("addcubes")[:150])
    time.sleep(2)
    out2 = step("read")
    fp2 = PROBE / "fd_direct.json"
    if fp2.exists():
        try:
            d2 = json.loads(fp2.read_text(encoding="utf-8"))
            print("   events=%s extra_hits=%s with_obj=%s" % (d2.get("events"), d2.get("extra_hits"), d2.get("with_obj")))
            for s in (d2.get("samples") or [])[:4]:
                print("   样本:", json.dumps(s, ensure_ascii=False)[:170])
            Path(ROOT / "runs" / "fd_direct_after.json").write_text(json.dumps(d2, ensure_ascii=False, indent=2), encoding="utf-8")
        except json.JSONDecodeError as e:
            print("   解析失败:", str(e)[:120], "| head:", out2[:300])

    print("\n[4] disable")
    print("   ", step("disable")[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
