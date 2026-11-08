#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m2a_overhead.py —— 常驻探针开销重标定（D12 全程常开的欠账，AGENTS §1.2）。

背景：M1' 旧结论 ≤1.83% 是"窗口内录制"口径，结构已变不迁移。新常驻件：
  UpiBreachProbe（每帧墙钟差 + GC recorder 读 + 预建数组写 + 低频区）
  ReplayDriver（每帧压 4 个 Time 设置 + 低频区）

三层（会话内顺序敏感）：
  L0 none  探针与驱动器全部 unload（参考态；scene open 触发域重载会使其
     自动重装 InitializeOnLoad ⇒ 必须每次开场景后立刻 unload）
  L1 idle  两者装载、探针未 set seed/threshold（= 全程常开稳态成本：
     Tick 每帧照跑，只是不判越线；A4 读取在 Play 中本就无法关，L1 已含）
  L2 full  L1 + thr/seed（判定与环形缓冲全量）

开销 = (median_Lx - median_L0) / median_L0，主判据 cpuFrameTimeMs。
两场景各测（含 F-CPU-01 分配敏感对照——M1' 教训：污染在分配指标显形，
gc_alloc 的 L1/L2 漂移必须单列观察）。超预算(5%) ⇒ instrument_distorted，
探针实现必须整改，不许放宽预算。
"""
import json
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "fixtures" / "upi-fixture"
PROBEDIR = ROOT / "runs" / "probe_overhead"
PROBEDIR.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "calibration" / "m2a_overhead.json"
BUDGET = 0.05
WINDOWS = 3
POLL_N = 25
PB_CTL = ROOT / "runs" / "_probe" / "pb_cmd.txt"
PB_RES = ROOT / "runs" / "_probe" / "pb_res.txt"
RP_CTL = ROOT / "runs" / "_probe" / "rp_cmd.txt"
RP_RES = ROOT / "runs" / "_probe" / "rp_res.txt"
FIXTURES = ["CLEAN", "F-CPU-01"]


def u(*args: str, t: int = 150) -> dict:
    p = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                       capture_output=True, text=True, timeout=t,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "errors": [{"message": (p.stdout or p.stderr)[:200]}]}


def c(name: str, *extra: str, t: int = 150) -> dict:
    return u("command", name, "--project-path", str(PROJ), *extra, t=t)


def ev(code: str, t: int = 220) -> str:
    r = c("script eval", "--code", code, t=t)
    if r.get("success"):
        return str((r.get("data") or {}).get("result", {}).get("result"))
    return "ERR " + str(r.get("errors", [{}])[0].get("message", ""))[:200]


def wait_ready(w: int = 240) -> bool:
    t0 = time.time()
    while time.time() - t0 < w:
        res = (c("editor status").get("data") or {}).get("result") or {}
        if res.get("status") == "ready" and not res.get("compiling") \
                and not res.get("domainReloadInProgress"):
            return True
        time.sleep(5)
    return False


def probe_direct(line: str) -> str:
    import base64
    b64 = base64.b64encode(line.encode()).decode()
    code = ("using System; using System.Reflection;"
            "Type t=null;"
            "foreach (var a in AppDomain.CurrentDomain.GetAssemblies())"
            "{ t=a.GetType(\"Upi.Fixtures.EditorTools.UpiBreachProbe\"); if (t!=null) break; }"
            "if (t==null) return \"ERR class\";"
            "string s=System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(\"" + b64 + "\"));"
            "var m=t.GetMethod(\"Direct\", BindingFlags.Public|BindingFlags.Static);"
            "return (string)m.Invoke(null,new object[]{s});")
    return ev(code)


def rp_ctl(line: str) -> str:
    old = RP_RES.read_text() if RP_RES.exists() else None
    RP_CTL.write_text(line)
    t0 = time.time()
    while time.time() - t0 < 10:
        time.sleep(0.4)
        if RP_RES.exists() and RP_RES.read_text() != old:
            return RP_RES.read_text()[:80]
    return "TIMEOUT"


def med_polls(play: bool, fixture: str) -> dict:
    if not play:
        c("editor.playmode stop")
        time.sleep(3)
    r = c("scene open", "--path", f"Assets/UpiFixtures/Scenes/Scene_{fixture}.unity", t=200)
    if not r.get("success"):
        return {"error": str(r.get("errors", [{}])[0].get("message", ""))[:120]}
    time.sleep(6)
    wait_ready()
    if play:
        c("editor.playmode start")
        time.sleep(7)
    vals, gcs = [], []
    for _ in range(POLL_N):
        d = ((c("diagnostics get_performance", t=90).get("data") or {}).get("result") or {})
        fr = d.get("frameTiming") or {}
        me = d.get("memory") or {}
        if fr.get("cpuFrameTimeMs") is not None:
            vals.append(float(fr["cpuFrameTimeMs"]))
        if me.get("totalAllocatedBytes") is not None:
            gcs.append(float(me["totalAllocatedBytes"]))
        time.sleep(1.0)
    if play:
        c("editor.playmode stop")
        time.sleep(4)
    if len(vals) < 15:
        return {"error": "poll_fail n=%d" % len(vals)}
    return {"cpuFrameTimeMs": st.median(vals), "n": len(vals),
            "totalAllocatedBytes": st.median(gcs) if gcs else None}


def one_window(play: bool, fixture: str) -> dict:
    rows = [med_polls(play, fixture) for _ in range(WINDOWS)]
    rows = [r for r in rows if "error" not in r]
    if not rows:
        return {"error": "全部窗口失败"}
    return {"cpuFrameTimeMs": st.median([r["cpuFrameTimeMs"] for r in rows]),
            "totalAllocatedBytes": st.median([r["totalAllocatedBytes"] for r in rows
                                              if r["totalAllocatedBytes"]]),
            "windows": len(rows)}


def cal_session(play: bool, fixture: str, layer: str) -> dict:
    """层内顺序：scene open 会域重载 ⇒ 先开场景再布置探针状态。"""
    c("editor.playmode stop")
    time.sleep(3)
    r = c("scene open", "--path", f"Assets/UpiFixtures/Scenes/Scene_{fixture}.unity", t=200)
    if not r.get("success"):
        return {"error": "scene open 失败"}
    time.sleep(6)
    wait_ready()
    if play:
        c("editor.playmode start")
        time.sleep(7)
    # 时序关键：scene open 与进 Play 都触发域重载 ⇒ InitializeOnLoad 必然重装
    # 两个常驻件（实测 T2 期同类坑）。布置必须在**进 Play 之后**，
    # L0 的 unload 才不会被重装覆盖；三态统一压环境基线也只压一次不计窗。
    ev('UnityEngine.Application.runInBackground = true;'
       'UnityEngine.Application.targetFrameRate = 60;'
       'return "env";')
    if layer == "L0":
        print("   probe:", probe_direct("unload")[:40], "| rp:", rp_ctl("unload")[:40])
    elif layer == "L1":
        print("   probe:", probe_direct("status")[:40])       # InitializeOnLoad 已在
        print("   rp status:", rp_ctl("status")[:40])
    elif layer == "L2":
        print("   probe:", probe_direct("status")[:40])
        # 阈值 200ms = 稳态水位之上（实测常态 16~24ms）：判定路径每帧全跑
        # （比较+环形写），但正常不越界落盘——测的是"全程常开稳态成本"，
        # 不是事件风暴成本。16ms 会天天越线，测出来的是写盘开销，口径错。
        probe_direct("thr:200000000")
        probe_direct("seed:20261108")
        rp_ctl("status")
    out = one_window_sample_only(play, fixture)
    if play:
        c("editor.playmode stop")
        time.sleep(4)
    return out


def one_window_sample_only(play: bool, fixture: str) -> dict:
    rows = []
    for _ in range(WINDOWS):
        vals, gcs = [], []
        for _ in range(POLL_N):
            d = ((c("diagnostics get_performance", t=90).get("data") or {}).get("result") or {})
            fr = d.get("frameTiming") or {}
            me = d.get("memory") or {}
            if fr.get("cpuFrameTimeMs") is not None:
                vals.append(float(fr["cpuFrameTimeMs"]))
            if me.get("totalAllocatedBytes") is not None:
                gcs.append(float(me["totalAllocatedBytes"]))
            time.sleep(1.0)
        if len(vals) >= 15:
            rows.append({"cpuFrameTimeMs": st.median(vals),
                         "totalAllocatedBytes": st.median(gcs) if gcs else None})
    if not rows:
        return {"error": "采样全失败"}
    return {"cpuFrameTimeMs": st.median([r["cpuFrameTimeMs"] for r in rows]),
            "totalAllocatedBytes": st.median([r["totalAllocatedBytes"] for r in rows]),
            "windows": len(rows)}


def main() -> int:
    if not wait_ready():
        print("编辑器不可达")
        return 2
    doc = {"budget_pct": BUDGET, "windows": WINDOWS, "poll_n": POLL_N,
           "play_mode": True, "fixtures": {}}
    for fx in FIXTURES:
        print(f"== {fx}（Play 态，真实帧节律）==")
        s = {}
        for layer in ("L0", "L1", "L2"):
            print(f" {layer}")
            s[layer] = cal_session(True, fx, layer)
            print("  ->", s[layer])
        if any("error" in s[l] for l in ("L0", "L1", "L2")):
            print("!! 该场景存在失败层，跳过（不产出半截结论）")
            continue
        base = s["L0"]["cpuFrameTimeMs"]
        s["install_pct"] = round((s["L1"]["cpuFrameTimeMs"] - base) / base * 100, 2)
        s["record_pct"] = round((s["L2"]["cpuFrameTimeMs"] - s["L1"]["cpuFrameTimeMs"])
                                / base * 100, 2)
        s["total_pct"] = round((s["L2"]["cpuFrameTimeMs"] - base) / base * 100, 2)
        gbase = s["L0"]["totalAllocatedBytes"]
        s["gc_alloc_drift_pct"] = round((s["L2"]["totalAllocatedBytes"] - gbase)
                                        / gbase * 100, 2)
        doc["fixtures"][fx] = s
    if not doc["fixtures"]:
        print("!! 无任何场景完成三层 → 不写入标定文件（缺项即无结论）")
        return 1
    mx = max(v["total_pct"] for v in doc["fixtures"].values())
    doc["instrument_distorted"] = mx > BUDGET * 100
    doc["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n==== 汇总（预算 %.0f%%）====" % (BUDGET * 100))
    for k, v in doc["fixtures"].items():
        print("  %-10s install=%6.2f%% record=%6.2f%% total=%6.2f%%  alloc漂移=%6.2f%%"
              % (k, v["install_pct"], v["record_pct"], v["total_pct"],
                 v["gc_alloc_drift_pct"]))
    print("判定:", "入预算" if not doc["instrument_distorted"] else "超预算 instrument_distorted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())