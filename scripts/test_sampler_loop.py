"""scripts/test_sampler_loop.py —— M0' 验收：常驻采样器完整闭环一次（GUI + Play Mode）。

实测决定的流程顺序（DESIGN §2.2.1 事实 5）：
  进 Play Mode 会触发域重载 ⇒ 采样器的静态状态与 update 回调**全部被清空**。
  若先装后进 Play，回调死掉而心跳文件仍是新鲜的 ⇒ 幂等判据误判"还活着"⇒ 永不重装。
  ⇒ 正确顺序：进 Play → 清心跳 → 安装 → 下 start → 轮询 → 退出 Play（finally 收尾）

另外：不设 targetFrameRate 时编辑器空转约 427 fps，300 帧窗口只覆盖 0.7 秒，
统计意义不足 ⇒ 固定 60 fps，窗口 300 帧 ≈ 5 秒。
"""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"
PROBE = ROOT / "runs" / "_probe"
SAMPLER = "sampler_v3.cs"
WINDOW = 300
TARGET_FPS = 60
sys.path.insert(0, str(ROOT))

from upi.tooling.trace import environment_fingerprint  # noqa: E402


def unity(*args: str, timeout: int = 90) -> dict:
    out = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:200]}]}


def cmd(name: str, *extra: str) -> dict:
    return unity("command", name, "--project-path", str(PROJECT), *extra)


def ev(code: str) -> str:
    r = cmd("script eval", "--code", code)
    if r.get("success"):
        return str(r["data"]["result"]["result"])
    return "ERR " + r.get("errors", [{}])[0].get("message", "")[:160]


def play_mode() -> str:
    d = cmd("editor status")
    return (d.get("data", {}).get("result") or {}).get("playMode", "unknown")


def robust(series: list[float]) -> dict:
    """DESIGN §3：中位数 + 稳健 MAD，禁止用均值/标准差。"""
    med = statistics.median(series)
    mad = statistics.median([abs(x - med) for x in series]) * 1.4826
    s = sorted(series)
    return {"n": len(series), "median": round(med, 4), "noise_band": round(mad, 4),
            "p5": round(s[max(0, int(len(s) * 0.05))], 4),
            "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 4)}


def main() -> int:
    src = ROOT / "probe" / SAMPLER
    dst = PROJECT / SAMPLER
    PROBE.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)

    if play_mode() == "playing":
        print("[0] 检测到残留 Play Mode，先退出")
        cmd("editor.playmode stop")
        time.sleep(4)

    rc = 1
    try:
        print("[1] 编辑态构建可渲染场景（退出 Play 会经域重载销毁物体，每轮必须重建）")
        mk = ROOT / "probe" / "build_scene.cs"
        shutil.copy(mk, PROJECT / "build_scene.cs")
        r0 = cmd("script eval_file", "--file", "build_scene.cs")
        print("   ", (r0.get("data", {}).get("result") or {}).get("result")
              if r0.get("success") else "失败: " + r0.get("errors", [{}])[0].get("message", "")[:200])
        (PROJECT / "build_scene.cs").unlink(missing_ok=True)

        print("[1a] 前置编辑器窗口（实测失焦时不绘制游戏视图 ⇒ 渲染计数器为 0）")
        print("   ", cmd("editor focus").get("data", {}).get("result", "失败"))

        print("[2] 进入 Play Mode（先于安装，避免域重载清空采样器）")
        print("   ", cmd("editor.playmode start").get("data", {}).get("result", "失败"))
        time.sleep(6)   # 等域重载结束
        print("   ", ev('UnityEngine.Application.runInBackground = true; '
                        'UnityEngine.Application.targetFrameRate = ' + str(TARGET_FPS) + '; '
                        'return "fps cap=" + UnityEngine.Application.targetFrameRate;'))

        print("[3] 清理探针目录（含心跳，强制全新安装）")
        for f in PROBE.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)

        print("[4] 安装采样器")
        r = cmd("script eval_file", "--file", SAMPLER)
        got = (r.get("data", {}).get("result") or {}).get("result") if r.get("success") else None
        print("   ", got if got else "失败: " + r.get("errors", [{}])[0].get("message", "")[:200])
        # installed=新装 / alive=上一轮的回调仍在正常工作（心跳新鲜即证明 Tick 在跑）
        # 两种都可用；只有既非 installed 也非 alive 才是真失败
        if not got or not ("installed" in str(got) or "alive" in str(got)):
            print("   !! 采样器不可用，中止")
            return rc

        print("[4a] 开启性能分析后端（实测：Memory 类目常开有值，Render 类目需 Profiler.enabled）")
        print("   ", ev('UnityEngine.Profiling.Profiler.enabled = true; '
                        'return "Profiler.enabled=" + UnityEngine.Profiling.Profiler.enabled;'))

        print(f"[5] 下发 start:{WINDOW}")
        (PROBE / "cmd.txt").write_text(f"start:{WINDOW}", encoding="utf-8")
        deadline = time.time() + 120
        data = None
        while time.time() < deadline:
            p = PROBE / "data.json"
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    break
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(1)
        if data is None:
            print("   !! 未取到 data.json")
            print("   heartbeat:", (PROBE / "heartbeat.txt").read_text()[:24]
                  if (PROBE / "heartbeat.txt").exists() else "无")
            print("   帧推进复查:", ev('return "frame=" + UnityEngine.Time.frameCount;'))
            return rc

        print(f"[6] 落盘成功 why={data['why']} play={data['play']} n={data['n']} "
              f"skipped={data.get('skipped')}")
        stats = {}
        raw = {}
        for k, v in data["series"].items():
            if len(v) >= 5:
                stats[k] = robust([float(x) for x in v])
                raw[k] = v[:12]
        for k, s in sorted(stats.items()):
            print(f"   {k:26s} n={s['n']:4d} median={s['median']:>12} noise={s['noise_band']:>10} p95={s['p95']:>12}")

        fp = environment_fingerprint(str(PROJECT))
        report = {"window_frames": WINDOW, "target_fps": TARGET_FPS, "collector": "sampler_v3 (A″ 备选通路，实测渲染读数为 0)",
                  "stats": stats, "sample_head": raw,
                  "editor_mode": "gui", "play_mode_during_sample": data["play"],
                  "skipped_counters": data.get("skipped", []),
                  "fingerprint": fp, "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        out = ROOT / "knowledge" / "sampler_loop_first.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        nonzero = [k for k, s in stats.items() if s["median"] > 0]
        print(f"[7] 结论：{len(stats)} 条有效序列，{len(nonzero)} 条中位数>0 → {nonzero}")
        print("   已写入", out.relative_to(ROOT))
        rc = 0 if stats else 1
        return rc
    finally:
        if play_mode() == "playing":
            print("[8] 退出 Play Mode（收尾）")
            cmd("editor.playmode stop")
        dst.unlink(missing_ok=True)
        print("[9] 已移除工程内临时副本")


if __name__ == "__main__":
    raise SystemExit(main())
