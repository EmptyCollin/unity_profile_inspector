"""scripts/run_exp4.py —— 决定性实验一键驱动。

回答两个悬案：
  Q1 启用 Profiler 模块后，渲染 counter 是否注册、能否按帧读到非零（注入式采样器复活判定）
  Q2 新录制的 .data 离线解析里，渲染列是否不再是 -1 哨兵（离线通路完整性判定）

顺序（每一步都是实测教训固化的）：
  重建场景(编辑态) → 聚焦窗口 → 进 Play → 清陈旧结果文件 → 安装 exp4
  → 等帧推进读实时采样 → 录制新 .data → 停 Play → 离线解析新 .data
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"
PROBE = ROOT / "runs" / "_probe"
CAPDIR = PROJECT / "ProfilerCaptures"


def unity(*args: str, timeout: int = 150) -> dict:
    out = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:200]}]}


def cmd(name: str, *extra: str, timeout: int = 150) -> dict:
    return unity("command", name, "--project-path", str(PROJECT), *extra, timeout=timeout)


def run_cs(local_cs: Path, label: str) -> str:
    dst = PROJECT / local_cs.name
    shutil.copy(local_cs, dst)
    r = cmd("script eval_file", "--file", local_cs.name, timeout=240)
    dst.unlink(missing_ok=True)
    if r.get("success"):
        return str(r["data"]["result"]["result"])
    return "[" + label + " 失败] " + str(r.get("errors", [{}])[0].get("message", ""))[:400]


def play_mode() -> str:
    d = cmd("editor status")
    return (d.get("data", {}).get("result") or {}).get("playMode", "unknown")


def main() -> int:
    if play_mode() == "playing":
        print("[0] 退出残留 Play Mode")
        cmd("editor.playmode stop")
        time.sleep(4)

    print("[1] 编辑态重建可渲染场景")
    print("   ", run_cs(ROOT / "probe" / "build_scene.cs", "build_scene").strip())

    print("[2] 聚焦窗口")
    print("   ", cmd("editor focus").get("data", {}).get("result", "失败"))

    print("[3] 进入 Play Mode 并等域重载")
    print("   ", cmd("editor.playmode start").get("data", {}).get("result", "失败"))
    time.sleep(7)
    print("   ", cmd("script eval", "--code",
                     'UnityEngine.Application.targetFrameRate = 60;'
                     'UnityEngine.Application.runInBackground = true;'
                     'return "fps=60 runBg=true play=" + UnityEngine.Application.isPlaying;')
          .get("data", {}).get("result", {}).get("result", ""))

    print("[4] 清陈旧结果并安装 exp4（启用模块 + 三组对照采样）")
    PROBE.mkdir(parents=True, exist_ok=True)
    for f in PROBE.glob("exp4_data.json*"):
        f.unlink(missing_ok=True)
    out = run_cs(ROOT / "probe" / "exp4_enable_modules.cs", "exp4")
    print(out)

    print("[5] 等 ~12 秒让按帧采样写盘")
    deadline = time.time() + 25
    data = None
    while time.time() < deadline:
        p = PROBE / "exp4_data.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data["n"] >= 60:
                    break
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(2)
    print("\n== Q1 实时按帧采样（模块启用后）==")
    if data is None:
        print("  未落盘（帧未推进或回调未装）")
    else:
        print(f"  n={data['n']} frame={data['frame']} play={data['play']}")
        for k, v in data["sets"].items():
            print(f"   {k:24s} last={v['last']:>12} max={v['max']:>12}")

    print("[6] 录制新 .data（120 帧）")
    old = {p.name for p in CAPDIR.glob("*.data")} if CAPDIR.exists() else set()
    r = cmd("diagnostics profiler_record", "--frames", "120")
    rec = (r.get("data") or {}).get("result") or {}
    print("   queued:", rec.get("status"), "|", Path(rec.get("path", "?")).name)
    for _ in range(20):
        time.sleep(4)
        s = ((cmd("diagnostics profiler_record_status").get("data") or {}).get("result") or {})
        if s.get("status") == "completed":
            print("   completed framesSaved=", s.get("framesSaved"))
            break

    print("[7] 停 Play")
    cmd("editor.playmode stop")
    time.sleep(5)

    print("[8] 离线解析最新 .data（复用 exp1 探针，它会自动取目录里第一个 .data——先确保选中最新）")
    files = sorted(CAPDIR.glob("*.data"), key=lambda p: p.stat().st_mtime)
    newest = files[-1]
    out2 = run_cs(ROOT / "probe" / "exp1_offline_data.cs", "exp1")  # exp1 自身按 mtime 取最新
    print("\n== Q2 离线逐帧解析（新录制，模块启用后）==")
    print(out2)

    rep = {"q1_live_sampling": data, "q2_offline_output": out2,
           "newest_capture": newest.name, "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (ROOT / "knowledge" / "exp4_results.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写入 knowledge/exp4_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
