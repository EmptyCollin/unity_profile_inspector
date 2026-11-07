"""scripts/diag_frames.py —— 诊断：失焦编辑器能否产出播放帧（M0' 阻塞点定位）。

实测问题：`get_performance` 有 render 真值（drawCalls≈185），但
Time.frameCount / Time.renderedFrameCount 在 Python 侧间隔取样期间完全不动。
⇒ 常驻采样器按 frameCount 门控就永远攒不满窗口，data.json 不会生成。

本脚本分别验证四个候选开关，逐个测帧增量：
  A 基线（当前状态）
  B Application.runInBackground = true
  C EditorApplication.update 自身能否推进帧（用递增计数法）
  D QueuePlayerLoopUpdate + Sleep 之外，改用 EditorApplication.delayCall 排程
"""
from __future__ import annotations

import json
import subprocess
import time

PROJECT = r"D:\unity_profile_inspector\fixtures\upi-fixture"


def unity_cmd(*args: str, timeout: int = 90) -> dict:
    out = subprocess.run(["unity", "command", *args, "--project-path", PROJECT,
                          "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:150]}]}


def ev(code: str) -> str:
    r = unity_cmd("script eval", "--code", code)
    if r.get("success"):
        return str(r["data"]["result"]["result"])
    return "ERR " + r.get("errors", [{}])[0].get("message", "")[:120]


def frame_pair(label: str, sleep_s: float = 4.0) -> None:
    """两次独立 eval 之间不阻塞主线程，测帧是否推进。"""
    a = ev('return UnityEngine.Time.frameCount + "/" + UnityEngine.Time.renderedFrameCount;')
    time.sleep(sleep_s)
    b = ev('return UnityEngine.Time.frameCount + "/" + UnityEngine.Time.renderedFrameCount;')
    try:
        f1, r1 = (int(x) for x in a.split("/"))
        f2, r2 = (int(x) for x in b.split("/"))
        print(f"  {label:24s} frame {f1}->{f2} (Δ{f2-f1})   rendered {r1}->{r2} (Δ{r2-r1})")
    except Exception:
        print(f"  {label:24s} 原始值 a={a} b={b}")


print("当前状态:", ev('return "play=" + UnityEngine.Application.isPlaying + " runBg=" + UnityEngine.Application.runInBackground;'))
print()
print("A 基线：")
frame_pair("baseline")

print("B runInBackground=true：")
print("   ", ev('UnityEngine.Application.runInBackground = true; return "set runBg=" + UnityEngine.Application.runInBackground;'))
frame_pair("runInBackground")

print("C 显式设 targetFrameRate（避免节流到极低帧）：")
print("   ", ev('UnityEngine.Application.targetFrameRate = 60; return "targetFrameRate=" + UnityEngine.Application.targetFrameRate;'))
frame_pair("targetFrameRate=60")

print("D 检查编辑器窗口是否最小化/被遮挡（决定重绘是否被跳过）：")
print("   ", ev('var w=UnityEditor.EditorWindow.focusedWindow; return "focused=" + (w==null?"null":w.title) + " isPlaying=" + UnityEditor.EditorApplication.isPlayingOrWillChangePlaymode;'))

print()
print("render 计数器当前值（与帧推进解耦，用于判断它到底统计什么）:")
r = unity_cmd("get_performance")
if r.get("success"):
    print("  ", json.dumps(r["data"]["result"]["render"], ensure_ascii=False))
print("连续两次（间隔 3 秒，其间不做任何 eval 阻塞）:")
time.sleep(3)
r2 = unity_cmd("get_performance")
if r2.get("success"):
    print("  ", json.dumps(r2["data"]["result"]["render"], ensure_ascii=False))
