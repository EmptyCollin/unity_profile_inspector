"""B 模式（浅档 profiler 常开 + 滚动清帧）开销测量。

测量口径（进程外，不依赖被测量对象自报）：
- CPU：编辑器进程 CPU 时间增量 / 实际帧数 = 每帧 CPU 毫秒（不受 vsync 封顶影响）
- 内存：进程 RSS 峰值与后 30 秒斜率（psapi，1Hz 采样）
- 粒度：浅档在越界帧窗口内能否给出项目内 marker/函数名

标准先定死（CRITERIA），判定由脚本自动给出。

用法：
    python scripts/bench_profiler.py            # 全量
    python scripts/bench_profiler.py --quick    # 冒烟（12s × 2 组）
    python scripts/bench_profiler.py --mem-only # 只测 60s 内存平台
结果：runs/bench_profiler_<ts>.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "runs" / "_probe"
CFG = PROBE / "bench_cfg.json"
OUT = PROBE / "bench_out.json"
BENCH = ROOT / "probe" / "upi_bench_frametime.cs"
PROJECT = ROOT / "sim" / "project"

# ── 可接受开销标准（先定后用，不按结果改）────────────────────────────
CRITERIA = {
    "cpu_median_delta_pct_max": 3.0,          # 不限帧下：帧时中位数增幅 ≤ 3%（主判据）
    "cpu_p95_delta_pct_max": 8.0,             # 不限帧下：p95 增幅 ≤ 8%
    "mem_peak_delta_mb_max": 400,             # 相对“全关”的 RSS 峰值增量上限
    "mem_slope_mb_per_min_max": 5.0,          # 长跑最后 60 秒的 RSS 斜率（平台判据）
    "frame_stall_ms_max": 500,                # 清帧造成的单帧停顿上限
    "granularity_required": True,              # 浅档必须能给出项目内 marker 名
}


# ── 进程外采样：RSS + CPU ────────────────────────────────────────────
class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)


def _open_proc(pid: int) -> int:
    h = _k32.OpenProcess(0x1000 | 0x0010, False, pid)   # QUERY_LIMITED | VM_READ
    if not h:
        raise OSError(f"OpenProcess 失败 pid={pid} err={ctypes.get_last_error()}")
    return h


def _rss_mb(h: int) -> float:
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    if not _psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
        return 0.0
    return c.WorkingSetSize / (1024.0 * 1024.0)


def _cpu_s(h: int) -> float:
    ft = [wintypes.FILETIME() for _ in range(4)]
    if not _k32.GetProcessTimes(h, ctypes.byref(ft[0]), ctypes.byref(ft[1]),
                                ctypes.byref(ft[2]), ctypes.byref(ft[3])):
        return 0.0
    kt = (ft[2].dwHighDateTime << 32) | ft[2].dwLowDateTime
    ut = (ft[3].dwHighDateTime << 32) | ft[3].dwLowDateTime
    return (kt + ut) / 1e7


class Sampler(threading.Thread):
    def __init__(self, h: int, interval: float = 1.0):
        super().__init__(daemon=True)
        self.h = h
        self.interval = interval
        self.stop = threading.Event()
        self.samples: list[dict] = []

    def run(self):
        while not self.stop.is_set():
            self.samples.append({"t": time.time(), "rss_mb": _rss_mb(self.h),
                                 "cpu_s": _cpu_s(self.h)})
            self.stop.wait(self.interval)


def editor_pid() -> int:
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" | "
          "Where-Object { $_.CommandLine -and $_.CommandLine -notmatch 'batchMode' "
          "-and $_.CommandLine -match 'sim[\\\\/]project' } | "
          "Select-Object -First 1 -ExpandProperty ProcessId")
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    s = (p.stdout or "").strip()
    return int(s) if s.isdigit() else 0


# ── Unity 指令 ──────────────────────────────────────────────────────
def _unity(*args, timeout: int = 120) -> dict:
    p = subprocess.run(["unity", "command", *args, "--project-path", str(PROJECT),
                        "--format", "json"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": (p.stdout or p.stderr)[-300:]}


def _eval(code: str, timeout: int = 60) -> dict:
    d = _unity("script eval", "--code", code, "--timeout", str(timeout))
    r = ((d.get("data") or {}).get("result") or {})
    return {"ok": bool(d.get("success") and r.get("success")),
            "result": r.get("result"), "errors": r.get("errors") or d.get("errors")}


def editor_alive() -> dict:
    d = _unity("editor status")
    s = ((d.get("data") or {}).get("result") or {}) if d.get("success") else {}
    return {"ok": bool(d.get("success")), "playMode": s.get("playMode"),
            "status": s.get("status")}


def ensure_play() -> dict:
    st = editor_alive()
    if not st.get("ok"):
        return st
    if st.get("playMode") != "playing":
        _unity("editor.playmode start")
        for _ in range(40):
            time.sleep(0.5)
            st = editor_alive()
            if st.get("playMode") == "playing":
                break
    _unity("editor set_auto_tick", "--enabled", "true", "--interval-ms", "16",
           "--persist", "false")
    return st


def restart_game() -> str:
    code = ('var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
            'if(g==null) return "no-game";'
            'g.DebugLoadConfig("{\\"seed\\":20261112,\\"autoplay\\":true,'
            '\\"round_seconds\\":300}"); g.DebugStartRun(); return g.DebugState();')
    r = _eval(code)
    return str(r.get("result") or r.get("errors") or "")[:120]


def phase_now() -> str:
    r = _eval('var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
              'if(g==null) return "no-game"; return g.DebugState();')
    v = r.get("result")
    if isinstance(v, str):
        try:
            return json.loads(v).get("phase", "?")
        except Exception:
            return "?"
    return "?"


def run_once(pid: int, mode: str, duration: float, uncapped: bool = False) -> dict:
    PROBE.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"profiler": mode, "duration_s": duration,
                               "clear_every_s": 2.0, "uncapped": uncapped}),
                   encoding="utf-8")
    try:
        OUT.unlink()
    except OSError:
        pass
    restart_game()
    time.sleep(0.5)
    # 不限帧时把 auto-tick 放到最密，否则编辑器 16ms 的 tick 会把帧率钉在 60。
    _unity("editor set_auto_tick", "--enabled", "true",
           "--interval-ms", "0" if uncapped else "16", "--persist", "false")
    h = _open_proc(pid)
    try:
        smp = Sampler(h)
        smp.start()
        d = _unity("script eval", "--file", str(BENCH), "--format", "json", timeout=120)
        if not d.get("success"):
            raise RuntimeError("bench eval 失败：" + json.dumps(d.get("errors"),
                                                              ensure_ascii=False)[:300])
        deadline = time.time() + duration + 60
        while time.time() < deadline and not OUT.exists():
            time.sleep(0.5)
        if not OUT.exists():
            if not editor_alive().get("ok"):
                raise RuntimeError("采集期间 Unity 编辑器失联（可能已退出）")
            raise RuntimeError("bench 结果未落盘")
        time.sleep(0.3)
        smp.stop.set()
        smp.join(timeout=3)
    finally:
        _k32.CloseHandle(h)
    r = json.loads(OUT.read_text(encoding="utf-8"))
    r["phase_after"] = phase_now()
    r["uncapped"] = uncapped
    r.update(_proc_metrics(smp.samples, r.get("frames", 0)))
    if uncapped:
        _unity("editor set_auto_tick", "--enabled", "true", "--interval-ms", "16",
               "--persist", "false")
    return r


def _proc_metrics(samples: list[dict], frames: int) -> dict:
    if len(samples) < 2:
        return {"proc": {"available": False}}
    cpu_s = samples[-1]["cpu_s"] - samples[0]["cpu_s"]
    rss = [s["rss_mb"] for s in samples if s["rss_mb"] > 0]
    return {"proc": {
        "available": bool(rss),
        "samples": len(samples),
        "cpu_s": cpu_s,
        "frames_for_cpu": frames,
        "cpu_ms_per_frame": (cpu_s * 1000.0 / frames) if frames else 0.0,
        "rss_start_mb": rss[0] if rss else 0.0,
        "rss_end_mb": rss[-1] if rss else 0.0,
        "rss_peak_mb": max(rss) if rss else 0.0,
        "rss_series": rss,
    }}


def _slope_per_min(series: list[float]) -> float:
    if len(series) < 30:
        return 0.0
    tail = series[-60:]
    n = len(tail)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(tail) / n
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, tail)) / den
    return b * 60.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--mem-only", action="store_true")
    a = ap.parse_args()

    st = ensure_play()
    if not st.get("ok"):
        print("编辑器不可用：", st)
        return 2
    pid = editor_pid()
    if not pid:
        print("找不到编辑器 PID")
        return 2
    print(f"editor pid={pid}")

    plan_cpu = [("off", 60), ("shallow", 60)]
    plan_mem = [("off", 60), ("shallow", 180)]
    if a.quick:
        plan_cpu, plan_mem = [("off", 10), ("shallow", 10)], []
    if a.mem_only:
        plan_cpu = []

    runs: list[dict] = []
    try:
        for rep in range(1 if a.quick else 3):
            for mode, dur in plan_cpu:
                print(f"[cpu rep{rep + 1}] {mode} {dur}s ...", flush=True)
                runs.append({"kind": "cpu", "rep": rep + 1,
                             **run_once(pid, mode, dur, uncapped=True)})
        for mode, dur in plan_mem:
            print(f"[mem] {mode} {dur}s ...", flush=True)
            runs.append({"kind": "mem", "rep": 1, **run_once(pid, mode, dur)})
    except Exception as e:  # noqa: BLE001
        print("测量中止：", e)
        return 1

    def agg(kind: str, mode: str, get):
        vals = [get(r) for r in runs if r["kind"] == kind and r["mode"] == mode]
        vals = [v for v in vals if v]
        return statistics.median(vals) if vals else 0.0

    cpu = {}
    for mode in ("off", "shallow", "shallow_alloc"):
        if not any(r["mode"] == mode for r in runs if r["kind"] == "cpu"):
            continue
        cpu[mode] = {
            "cpu_ms_per_frame": agg("cpu", mode, lambda r: r["proc"].get("cpu_ms_per_frame")),
            "wall_median": agg("cpu", mode, lambda r: r["frame_ms"]["median"]),
            "wall_p95": agg("cpu", mode, lambda r: r["frame_ms"]["p95"]),
        }
    base = cpu.get("off", {})
    for mode, c in cpu.items():
        if mode == "off" or not base.get("wall_median"):
            continue
        c["wall_median_delta_pct"] = ((c["wall_median"] - base["wall_median"])
                                      / base["wall_median"] * 100)
        c["wall_p95_delta_pct"] = ((c["wall_p95"] - base["wall_p95"])
                                   / base["wall_p95"] * 100) if base.get("wall_p95") else 0.0
        if base.get("cpu_ms_per_frame"):
            c["cpu_delta_pct"] = ((c["cpu_ms_per_frame"] - base["cpu_ms_per_frame"])
                                  / base["cpu_ms_per_frame"] * 100)

    mem = {}
    for mode in ("off", "shallow"):
        rs = [r for r in runs if r["kind"] == "mem" and r["mode"] == mode]
        if not rs:
            continue
        r = rs[0]
        series = r["proc"].get("rss_series") or []
        mem[mode] = {"peak_mb": r["proc"].get("rss_peak_mb", 0),
                     "end_mb": r["proc"].get("rss_end_mb", 0),
                     "start_mb": r["proc"].get("rss_start_mb", 0),
                     "slope_mb_per_min": _slope_per_min(series)}
    if "off" in mem and "shallow" in mem:
        mem["peak_delta_mb"] = mem["shallow"]["peak_mb"] - mem["off"]["peak_mb"]

    names = sorted({n for r in runs if r["mode"] == "shallow"
                    for n in r.get("sample_names", [])})
    stalls = max((r["frame_ms"]["max"] for r in runs), default=0.0)

    checks = {}
    if "shallow" in cpu and base.get("wall_median"):
        checks["cpu_median"] = (cpu["shallow"]["wall_median_delta_pct"]
                                <= CRITERIA["cpu_median_delta_pct_max"])
        checks["cpu_p95"] = (cpu["shallow"]["wall_p95_delta_pct"]
                             <= CRITERIA["cpu_p95_delta_pct_max"])
    if "peak_delta_mb" in mem:
        checks["mem_peak"] = mem["peak_delta_mb"] <= CRITERIA["mem_peak_delta_mb_max"]
        checks["mem_slope"] = (mem["shallow"]["slope_mb_per_min"]
                               <= CRITERIA["mem_slope_mb_per_min_max"])
    if names or CRITERIA["granularity_required"]:
        checks["granularity"] = bool(names)
    checks["stability"] = stalls <= CRITERIA["frame_stall_ms_max"]

    report = {"criteria": CRITERIA, "cpu": cpu, "memory": mem,
              "granularity_names": names[:20], "max_frame_ms": stalls,
              "checks": checks,
              "all_pass": all(v for v in checks.values() if v is not None),
              "runs": runs, "editor_pid": pid,
              "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    p = ROOT / "runs" / f"bench_profiler_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- 结果（CPU 为不限帧墙钟，避免 vsync 钉在 16.7ms）---")
    if base:
        print(f"CPU off     median={base['wall_median']:.2f}ms p95={base['wall_p95']:.2f}ms "
              f"(进程 {base['cpu_ms_per_frame']:.2f}ms/frame 仅供参考)")
    for mode in ("shallow", "shallow_alloc"):
        c = cpu.get(mode)
        if not c or not c.get("wall_median"):
            continue
        print(f"CPU {mode:13s} median={c['wall_median']:.2f}ms "
              f"({c.get('wall_median_delta_pct', 0):+.1f}%) "
              f"p95={c['wall_p95']:.2f}ms ({c.get('wall_p95_delta_pct', 0):+.1f}%) "
              f"进程 {c['cpu_ms_per_frame']:.2f}ms/frame ({c.get('cpu_delta_pct', 0):+.1f}%)")
    if mem:
        for mode in ("off", "shallow"):
            m = mem.get(mode)
            if m:
                print(f"MEM {mode:7s} start={m['start_mb']:.0f} peak={m['peak_mb']:.0f} "
                      f"end={m['end_mb']:.0f} slope={m['slope_mb_per_min']:+.1f}MB/min")
        if "peak_delta_mb" in mem:
            print(f"MEM peak Δ {mem['peak_delta_mb']:+.0f}MB")
    print(f"粒度 marker 名 {len(names)} 个，例：{names[:3]}")
    print(f"最大单帧 {stalls:.0f}ms")
    print("判定：" + "，".join(
        f"{k}={'SKIP' if v is None else ('PASS' if v else 'FAIL')}"
        for k, v in checks.items()))
    print(f"ALL_PASS {report['all_pass']}")
    print(f"EVIDENCE {p}")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
