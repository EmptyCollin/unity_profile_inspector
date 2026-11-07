"""M1' 标定严谨性：多窗口噪声带 + probe_overhead 双会话对比。

背景（本轮要修的两个缺陷）：
  1. 原 noise_band 来自"单个 30 次轮询窗口内部"的 MAD——那度量的是窗口内抖动；
     DESIGN §3 定义的噪声带应是"同一协议重复采样（窗口间）的 σ"。
  2. probe_overhead（采样器自身对帧时间的扰动）从未标定 ⇒ instrument_distorted 门禁形同虚设。

⚠️ 会话约束（实测教训）：A2 注入回调装入后无法从新 eval 卸载（§2.2.1 事实 2），
   所以"A 单独"与"A+A2"两组数据必须来自两次冷会话 ⇒ 脚本以 --session 区分，
   会话之间由外部重启编辑器（优雅退出 editor quit，禁止按名字杀进程）。

用法：
  python scripts/m1_multiwin.py --session none --fixtures F-CPU-01 F-GPU-01 --windows 4
  （重启编辑器后）
  python scripts/m1_multiwin.py --session full --windows 5    # 五场景全量
  python scripts/m1_multiwin.py --merge                       # 汇总出阈值+开销报告
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROJECT = ROOT / "fixtures" / "upi-fixture"
SCENES_DIR = PROJECT / "Assets/UpiFixtures/Scenes"
PROBE = ROOT / "runs/_probe"
CALDIR = ROOT / "calibration"
FULL_SESSION = False
IDLE_ONLY = False

WARMUP = 8            # 每窗口预热丢弃的轮询次数
WINDOW = 20           # 每窗口轮询次数（多窗口设计下缩短单窗口）
POLL = 1.0            # 目标轮询间隔（秒）
ALL_FIXTURES = ["F-CPU-01", "F-GPU-01", "F-ANIM-01", "F-LOAD-01", "CLEAN"]
WINDOWS_FULL = 4      # 全量会话窗口数（n=4≥3，窗口间 σ 成立）

# 复用 m1_baseline 的注入源码（采样器已含 gc/mem/anim/graph 四路，文件控制协议+nonce）
from scripts.m1_baseline import SAMPLER_SRC  # noqa: E402


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


def ev(code: str, timeout: int = 220) -> str:
    r = cmd("script eval", "--code", code, timeout=timeout)
    return str(r["data"]["result"]["result"]) if r.get("success") else "ERR " + str(r.get("errors", [{}])[0].get("message", ""))[:200]


def wait_ready(max_wait: int = 180) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        r = cmd("editor status")
        res = (r.get("data") or {}).get("result") or {}
        if res.get("status") == "ready" and not res.get("compiling") and not res.get("domainReloadInProgress"):
            return True
        time.sleep(5)
    return False


def robust(series: list[float]) -> dict:
    med = statistics.median(series)
    mad = statistics.median([abs(x - med) for x in series]) * 1.4826
    uniq = len(set(series))
    return {"n": len(series), "median": med, "mad": mad,
            "unique": uniq, "degenerate": uniq / max(1, len(series)) < 0.1}


def one_window(fixture: str, nonce: str) -> dict | None:
    """一个完整窗口：预热 + WINDOW 次轮询 +（full 会话）A2 逐帧录制。"""
    samples: dict[str, list[float]] = {}
    for _ in range(WARMUP):
        time.sleep(POLL)
        cmd("get_performance", timeout=60)

    a2 = None
    if FULL_SESSION and not IDLE_ONLY:
        (PROBE / "a2_cmd.txt").write_text(f"start:{nonce}", encoding="utf-8")
        time.sleep(1.2)

    t = time.time()
    for _ in range(WINDOW):
        t_loop = time.time()
        r = cmd("get_performance", timeout=60)
        res = (r.get("data") or {}).get("result") or {}
        for grp in ("render", "memory", "frameTiming"):
            for k, v in (res.get(grp) or {}).items():
                if isinstance(v, (int, float)):
                    samples.setdefault(k, []).append(float(v))
        left = POLL - (time.time() - t_loop)
        if left > 0:
            time.sleep(left)

    if FULL_SESSION and not IDLE_ONLY:
        (PROBE / "a2_cmd.txt").write_text(f"stop:{nonce}", encoding="utf-8")
        fp = PROBE / "a2_frame.json"
        deadline = time.time() + 15
        while time.time() < deadline:
            if fp.exists():
                try:
                    if fp.stat().st_mtime >= t:      # 拒绝陈旧（幽灵防护）
                        parsed = json.loads(fp.read_text(encoding="utf-8").replace(",]", "]"))
                        if parsed.get("nonce") == nonce:
                            a2 = parsed
                            break
                except (OSError, json.JSONDecodeError):
                    pass
                fp.unlink(missing_ok=True)
            time.sleep(0.5)
        if a2 is None:
            print("      !! A2 窗口数据缺失（nonce 不匹配或超时）")

    return {"wall_s": round(time.time() - t, 2), "poll_n": len(samples.get("drawCalls", [])),
            "metrics": {k: robust(v) for k, v in samples.items() if len(v) >= 5},
            "a2": a2}


def sample_scene(fixture: str, windows: int) -> dict:
    scene = SCENES_DIR / f"Scene_{fixture}.unity"
    print(f"  打开 Scene_{fixture}")
    cmd("scene open", "--path", f"Assets/UpiFixtures/Scenes/Scene_{fixture}.unity")
    time.sleep(5)
    if not wait_ready():
        return {"error": "编辑器未就绪"}
    print(f"  进 Play:", cmd("editor.playmode start").get("data", {}).get("result", "失败"))
    time.sleep(6)
    ev('UnityEngine.Application.targetFrameRate = -1; UnityEngine.Application.runInBackground = true; return "uncapped";')

    if FULL_SESSION:
        out = ev(SAMPLER_SRC, timeout=260)
        print("  A2 注入:", out[:90])
        if out.startswith("ERR") or not ("ready" in out or "alive" in out):
            cmd("editor.playmode stop"); time.sleep(4)
            return {"error": "A2 注入失败: " + out[:200]}

    wins = []
    for i in range(windows):
        nonce = f"{fixture}-{i}-{int(time.time())}"
        print(f"    窗口 {i + 1}/{windows} …")
        w = one_window(fixture, nonce)
        if w:
            wins.append(w)
    cmd("editor.playmode stop")
    time.sleep(5)
    if not scene.exists():
        return {"error": "场景文件缺失"}
    return {"fixture": fixture, "scene": scene.name, "windows": wins}


def main() -> int:
    global FULL_SESSION, IDLE_ONLY
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", choices=["none", "idle", "full"])
    ap.add_argument("--fixtures", nargs="*", default=None)
    ap.add_argument("--windows", type=int, default=5)
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args()

    CALDIR.mkdir(exist_ok=True)

    if args.merge:
        return do_merge()

    FULL_SESSION = args.session in ("idle", "full")
    IDLE_ONLY = args.session == "idle"
    # 会话健康自检：none 会话必须没有活动回调（心跳不新鲜），否则数据被污染
    hb = PROBE / "a2_hb.txt"
    if args.session == "none":
        fresh = hb.exists() and (time.time() - hb.stat().st_mtime) < 4
        if fresh:
            print("!! none 会话检测到活动中的采样器心跳——会话不干净，需重启编辑器后重试")
            return 2
    for f in (PROBE / "a2_frame.json", PROBE / "a2_cmd.txt"):
        f.unlink(missing_ok=True)

    fixtures = args.fixtures or (ALL_FIXTURES if FULL_SESSION else ["F-CPU-01", "F-GPU-01"])
    doc = {"session": args.session, "windows_target": args.windows,
           "protocol": {"warmup": WARMUP, "window_polls": WINDOW, "poll_interval_s": POLL},
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "fixtures": {}}
    for fx in fixtures:
        print(f"[{fx}]")
        doc["fixtures"][fx] = sample_scene(fx, args.windows)
        fp = CALDIR / f"multiwin_{args.session}.json"
        fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写入", CALDIR / f"multiwin_{args.session}.json")
    return 0


def between_windows(wins: list[dict]) -> dict:
    """跨窗口的窗口值序列 → 真·重复采样 σ（§3 定义）。"""
    keys = set()
    for w in wins:
        keys.update(w["metrics"].keys())
    out = {}
    for k in keys:
        vals = [w["metrics"][k]["median"] for w in wins if k in w["metrics"]]
        if len(vals) >= 3:
            out[k] = robust([float(x) for x in vals])
    return out


def do_merge() -> int:
    fp_full = CALDIR / "multiwin_full.json"
    if not fp_full.exists():
        print("缺 multiwin_full.json")
        return 1
    full = json.loads(fp_full.read_text(encoding="utf-8"))
    clean = full["fixtures"].get("CLEAN", {})
    if "error" in clean or not clean.get("windows"):
        print("full 会话 CLEAN 数据缺失")
        return 1
    bw = between_windows(clean["windows"])
    a2keys = ("gc", "anim", "mem", "graph")

    # A2 逐帧指标：窗口 med 序列的 MAD（窗口内样本 ~7800 帧，中位数稳定）
    a2_bw = {}
    for k2, name in (("gc", "gc_alloc_per_frame"), ("anim", "animator_process_animations_ns"),
                     ("graph", "animator_process_graph_ns"), ("mem", "total_used_per_frame")):
        vals = []
        for w in clean.get("windows", []):
            a2 = w.get("a2") or {}
            seq = a2.get(k2, [])
            if len(seq) >= 50:
                vals.append(float(statistics.median(seq)))
        if len(vals) >= 3:
            a2_bw[name] = robust(vals)

    FLOORS = {"gc_alloc_per_frame": 20_000, "animator_process_animations_ns": 50_000,
              "animator_process_graph_ns": 50_000, "drawCalls": 10, "setPassCalls": 8,
              "cpuFrameTimeMs": 0.5, "cpuMainThreadFrameTimeMs": 0.3,
              "totalAllocatedBytes": 2_000_000, "monoUsedBytes": 2_000_000, "batches": 5}

    merged = {**bw, **a2_bw}
    thresholds = {}
    for k, st in merged.items():
        floor = FLOORS.get(k, 0.0)
        thresholds[k] = {
            "metric": k, "clean_median": round(st["median"], 4),
            "clean_band": round(st["mad"], 6), "band_basis": f"between_windows(n={st['n']})",
            "min_magnitude": round(max(floor, 6 * st["mad"]), 4),
            "deviation_ratio": round(1.0 + 3 * (st["mad"] / st["median"] if st["median"] > 1 else 1.0), 4),
            "k_sigma": 3.0, "degenerate": st["degenerate"],
            "basis": "窗口间 σ + 指标地板（§8.1，M1' 多窗口标定）",
        }

    # probe_overhead：full 相对 a_only 的帧时间抬升
    overhead = None
    fp_idle = CALDIR / "multiwin_idle.json"
    if fp_idle.exists():
        idle = json.loads(fp_idle.read_text(encoding="utf-8"))
        rows = []
        for fx in ("F-CPU-01", "F-GPU-01"):
            wn = None
            fp_none = CALDIR / "multiwin_none.json"
            if fp_none.exists():
                wn = json.loads(fp_none.read_text(encoding="utf-8"))["fixtures"].get(fx, {}).get("windows", [])
            wi = idle["fixtures"].get(fx, {}).get("windows", [])
            wf = full["fixtures"].get(fx, {}).get("windows", [])
            if not wn:  # 无 none 层时退化为 idle vs full 两层
                continue
            c = {name: between_windows(wins).get("cpuFrameTimeMs")
                 for name, wins in (("none", wn), ("idle", wi), ("full", wf))}
            if all(c.get(k) and c[k]["median"] > 0 for k in ("none", "idle", "full")):
                base = c["none"]["median"]
                rows.append({"fixture": fx,
                             "none_ms": round(base, 3), "idle_ms": round(c["idle"]["median"], 3),
                             "full_ms": round(c["full"]["median"], 3),
                             "install_pct": round((c["idle"]["median"] - base) / base * 100, 2),
                             "record_pct": round((c["full"]["median"] - c["idle"]["median"]) / base * 100, 2),
                             "total_pct": round((c["full"]["median"] - base) / base * 100, 2)})
        if rows:
            overhead = {"layers": rows, "basis": "none/idle/full 三层",
                        "max_total_pct": max(r["total_pct"] for r in rows),
                        "limit_pct": 5.0}

    out = {"source": fp_full.name, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "protocol": full["protocol"], "thresholds": thresholds}
    if overhead:
        out["probe_overhead"] = overhead
    blob = json.dumps(out, sort_keys=True).encode()
    h = hashlib.sha256(blob).hexdigest()[:12]
    dest = ROOT / "baselines" / f"thresholds_{h}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"合并完成 → {dest.name}")
    print(f"  阈值 {len(thresholds)} 项（窗口间 σ，n={max((t['band_basis'].split('n=')[1] for t in thresholds.values() if 'n=' in t['band_basis']), default='?')}）")
    if overhead:
        print("  probe_overhead（三层分解）:")
        for r in overhead["layers"]:
            print(f"    {r['fixture']}: none={r['none_ms']} idle={r['idle_ms']} full={r['full_ms']}ms"
                  f" | 安装={r['install_pct']}% 录制={r['record_pct']}% 合计={r['total_pct']}%")
        mx = overhead["max_total_pct"]
        flag = "超限!! 该会话 bundle 应标 instrument_distorted" if mx > overhead["limit_pct"] else "在预算内"
        print(f"    最大合计 {mx}% / 预算 {overhead['limit_pct']}% ⇒ {flag}")
    else:
        print("  probe_overhead: 缺 idle 层或 none 层数据（三层不全，无法归因分解）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
