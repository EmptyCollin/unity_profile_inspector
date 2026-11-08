#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a4_altertest.py —— A4 拆弹实验：交替块分离"修复真实代价"与"会话时长污染"。

设计（对应认知债 A4 的修法）：
  块序 A1(缺陷) → B1(修复) → A2(缺陷) → B2(修复)，块间 git 换代码态 +
  重编译（触发域重载=会话年龄归零，成为对照组共享的边界事件）。

  每块两个测量，协议对称：
  ① 工作束：m1_multiwin full 会话 2 窗口（每窗 预热8+轮询20），
     窗口中位数序列——块内窗序在 A/B 同为 1→2，时序在块间对比中相消。
  ② 地板束：工作束结束后重新进 Play（年龄归零，双方公平）→
     采 8 次 memory → GC.Collect×2 → 再采 8 次。pre/post 中位数。

  判读：
    Δfix(总)      = ((B1-A1)+(B2-A2))/2      ← 含时长污染的旧口径
    Δfloor(净)    = post(B2)-post(A2)        ← 修复真实常驻（GC 收不走）
    Δgarbage(假象) = Δfix(总) - Δfloor 校正   ← 未回收垃圾份额
    时长趋势      = A2-A1（同为缺陷态、位置不同时）
  若 Δfix(总)≈Δfloor ⇒ 上升是修复真实代价；若 Δfix≫Δfloor ⇒ 大头是
  GC 行为改变导致的观测垃圾积累（上轮被否证的假设复活一半，见数据说话）。

成本：约 15 分钟（4 块 × [换态编译 60s + 采样 70s + 地板 40s]）。
产物：runs/a4_altertest_<stamp>/blocks/*.json + summary.json
"""
import json
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.m3_rehearsal import PLAN, CSFILE, PROJECT  # noqa: E402  复用修复方案
from upi.agents import fixer                            # noqa: E402
from upi.tooling.unity_cli import run_unity             # noqa: E402

PY = sys.executable
KEYS = ["monoUsedBytes", "totalAllocatedBytes", "totalReservedBytes",
        "monoHeapBytes", "cpuFrameTimeMs"]


def cli(name, *extra, t=120):
    return run_unity(["command", name, "--project-path", str(PROJECT), *extra],
                     timeout_s=t)


def set_state(kind: str) -> dict:
    """缺陷态=git checkout HEAD 版；修复态=在其上 apply PLAN（幂等重建）。"""
    subprocess.run(["git", "checkout", "HEAD", "--", CSFILE], cwd=str(ROOT),
                   check=True, capture_output=True)
    if kind == "fixed":
        fixer.apply_diffs(PLAN, root=ROOT)
    return fixer.compile_gate(project=PROJECT)     # 含域重载，年龄归零


def work_bundle(tag: str, run_dir: Path) -> list[dict]:
    out_dir = run_dir / "blocks" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([PY, str(ROOT / "scripts" / "m1_multiwin.py"),
                    "--session", "full", "--fixtures", "F-CPU-01",
                    "--windows", "2", "--caldir", str(out_dir), "--no-summary"],
                   capture_output=True, text=True, timeout=900,
                   encoding="utf-8", errors="replace")
    d = json.loads((out_dir / "multiwin_full.json").read_text(encoding="utf-8"))
    rows = []
    for w in d["fixtures"]["F-CPU-01"]["windows"]:
        r = {k: w["metrics"][k]["median"] for k in KEYS if k in w.get("metrics", {})}
        a2 = w.get("a2") or {}
        if a2.get("gc"):
            r["gc_alloc_per_frame"] = st.median(a2["gc"])
        rows.append(r)
    return rows


def mem_poll(n: int) -> dict:
    vals: dict[str, list[float]] = {k: [] for k in KEYS[:4]}
    for _ in range(n):
        res = (cli("diagnostics get_performance", t=60).data or {}).get("result") or {}
        mem = res.get("memory") or {}
        for k in vals:
            if isinstance(mem.get(k), (int, float)):
                vals[k].append(float(mem[k]))
        time.sleep(0.6)
    return {k: st.median(v) for k, v in vals.items() if v}


def floor_bundle() -> dict:
    """独立 fresh Play：pre → GC×2 → post。A/B 块协议完全对称。"""
    cli("editor.playmode start", t=90)
    time.sleep(8)
    pre = mem_poll(8)
    cli("script eval", "--code",
        "System.GC.Collect(); System.GC.WaitForPendingFinalizers();"
        "System.GC.Collect(); return \"gc\";", t=90)
    time.sleep(2)
    post = mem_poll(8)
    cli("editor.playmode stop", t=60)
    time.sleep(4)
    return {"pre": pre, "post": post}


def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "runs" / f"a4_altertest_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {"stamp": stamp, "blocks": {}}

    for tag, kind in (("A1", "defect"), ("B1", "fixed"),
                      ("A2", "defect"), ("B2", "fixed")):
        print(f"== 块 {tag}（{kind}）==", flush=True)
        t0 = time.time()
        cg = set_state(kind)
        if not cg["ok"]:
            print("!! 编译失败，中止:", cg["errors"][:2])
            return 1
        wins = work_bundle(tag, run_dir)
        floor = floor_bundle()
        doc["blocks"][tag] = {"kind": kind, "windows": wins, "floor": floor,
                              "block_seconds": round(time.time() - t0)}
        print(f"   窗1/窗2 monoUsed: {[round(w.get('monoUsedBytes',0)/1e6,1) for w in wins]}"
              f"  floor pre/post: {round(floor['pre']['monoUsedBytes']/1e6,1)}"
              f"/{round(floor['post']['monoUsedBytes']/1e6,1)} MB", flush=True)

    # ---- 收尾：回到缺陷态（HEAD 版本），不留残改动 ----
    subprocess.run(["git", "checkout", "HEAD", "--", CSFILE], cwd=str(ROOT),
                   check=True, capture_output=True)
    fixer.compile_gate(project=PROJECT)

    # ---- 汇总判读 ----
    b = doc["blocks"]
    def med(tag, src, key):
        seq = src(tag, key)
        return seq
    def win_med(tag, key):
        vs = [w[key] for w in b[tag]["windows"] if key in w]
        return st.median(vs) if vs else None
    def flr(tag, phase, key):
        return b[tag]["floor"][phase].get(key)

    doc["analysis"] = {}
    print("\n==== 判读 ====", flush=True)
    for key in KEYS[:4]:
        d_fix = None
        pairs = [(win_med("B1", key) - win_med("A1", key)),
                 (win_med("B2", key) - win_med("A2", key))]
        if all(p is not None for p in pairs) and all(win_med(t, key) for t in ("A1", "A2")):
            d_fix = st.mean(pairs)
            rel_fix = d_fix / win_med("A1", key)
        d_drift = (win_med("A2", key) - win_med("A1", key)) if win_med("A1", key) else None
        fl = flr("B2", "post", key), flr("A2", "post", key)
        d_floor = (fl[0] - fl[1]) if all(x is not None for x in fl) else None
        row = {"delta_fix_MB": round(d_fix / 1e6, 2) if d_fix is not None else None,
               "delta_rel_pct": round(rel_fix * 100, 2) if d_fix is not None else None,
               "defect_drift_A2_A1_MB": round(d_drift / 1e6, 2) if d_drift is not None else None,
               "delta_floor_post_MB": round(d_floor / 1e6, 2) if d_floor is not None else None}
        doc["analysis"][key] = row
        print(f"  {key:22s} Δ修复={row['delta_fix_MB']}MB 相对={row['delta_rel_pct']}%  "
              f"缺陷块漂移={row['defect_drift_A2_A1_MB']}MB  "
              f"Δ地板(净常驻)={row['delta_floor_post_MB']}MB", flush=True)
    # gc 行为侧证：修复块 floor 的 pre-post 差 vs 缺陷块（回收活动量代理）
    for tag in ("A2", "B2"):
        pre, post = flr(tag, "pre", "monoUsedBytes"), flr(tag, "post", "monoUsedBytes")
        doc["analysis"][f"gc_reclaim_{tag}_MB"] = round((pre - post) / 1e6, 2)
        print(f"  {tag} 块单次GC可回收: {doc['analysis'][f'gc_reclaim_{tag}_MB']}MB", flush=True)

    (run_dir / "summary.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n产物:", run_dir / "summary.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
