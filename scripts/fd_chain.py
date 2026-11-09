#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fd_chain.py —— 按用户提出的链路做分阶段验证

用户的假设：Frame Debugger 启用时会暂停游戏，但"手动继续"之后它还能接着跑；
所以链路应当是：探针响 → 抓当前帧 FD 数据 → resume → 不影响后续运行。

本脚本把这个假设拆成可单独证伪的步骤，每步只做一个动作、单独取证据：

  S0 基线活性：帧号与场景物体位置是否随真实时间推进（排除"编辑器本身不出帧"这一混淆）
  S1 开启抓取：只调 SetEnabled(true)，记录 enabled / isPaused / count
  S2 解除暂停：只调 isPaused=false，记录是否真的恢复出帧
  S3 继续跑一段时间后抓取：帧事件是否非空、能否解析、能否定位 GameObject
  S4 关掉抓取：记录是否恢复正常节奏（评估观察者效应）

判据全部来自读数，不做任何推断性结论。
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJ = ROOT / "fixtures" / "upi-fixture"
PY = sys.executable
OUT = ROOT / "runs" / "fd_chain_report.json"

CS_DIR = ROOT / "probe" / "fd_chain"


UNITY = "unity"


def evalc(code: str, timeout: int = 200):
    """把 C# 片段写进工程根并执行，返回 stdout 或错误串。"""
    tmp = PROJ / "fd_chain_eval.cs"
    shutil.copyfile(code, tmp)
    try:
        p = subprocess.run(
            [UNITY, "command", "script eval_file",
             "--project-path", str(PROJ), "--file", "fd_chain_eval.cs",
             "--format", "json", "--no-banner", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 60,
            encoding="utf-8", errors="replace")
        try:
            d = json.loads(p.stdout)
        except Exception:
            return "非 JSON 输出: " + (p.stdout or p.stderr)[:400]
        if not d.get("success"):
            return "编译/执行失败: " + (d["errors"][0].get("message", "")[:600] if d.get("errors") else "?")
        return d["data"]["result"]["result"]
    finally:
        tmp.unlink(missing_ok=True)


import os  # noqa: E402  (放在函数后是为了保持阅读顺序，实际使用在 evalc 内)

def cli(*args, timeout: int = 200):
    p = subprocess.run([UNITY] + list(args) + ["--project-path", str(PROJ),
                        "--format", "json", "--no-banner"],
                       capture_output=True, text=True, timeout=timeout + 60,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "errors": [{"message": (p.stdout or p.stderr)[:300]}]}


def wait_ready(sec: int = 420):
    end = time.time() + sec
    while time.time() < end:
        time.sleep(15)
        p = subprocess.run(
            [UNITY, "command", "editor status", "--project-path", str(PROJ),
             "--format", "json", "--no-banner"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        try:
            r = json.loads(p.stdout)["data"]["result"]
        except Exception:
            continue
        if r.get("status") == "ready" and not r.get("compiling") and not r.get("domainReloadInProgress"):
            return r
    return None


def main():
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}
    st = wait_ready()
    report["editor_ready"] = bool(st)
    print("编辑器就绪:", bool(st), flush=True)
    if not st:
        report["final"] = "编辑器未就绪，实验未开始"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    for key, script, note in [
        ("S0_liveness", CS_DIR / "s0.cs", "基线活性：帧号/位置/是否暂停"),
        ("S1_enable", CS_DIR / "s1.cs", "只开启抓取，不碰暂停"),
        ("S2_resume", CS_DIR / "s2.cs", "只解除暂停"),
        ("S3_read", CS_DIR / "s3.cs", "继续跑之后读当帧事件"),
        ("S4_disable", CS_DIR / "s4.cs", "关闭抓取并复核活性"),
    ]:
        print("--- %s %s" % (key, note), flush=True)
        t0 = time.time()
        out = evalc(script)
        report["steps"][key] = {"elapsed_s": round(time.time() - t0, 1), "out": out}
        print((out or "(空)")[:1400], flush=True)
        # S2 之后留真实时间出帧，模拟"手动继续，游戏接着跑"
        if key == "S2_resume":
            print("    [Python 侧等待 8 秒，让游戏在无阻塞下继续渲染]", flush=True)
            time.sleep(8)
            report["steps"][key]["waited_s"] = 8

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告已写入 %s" % OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
