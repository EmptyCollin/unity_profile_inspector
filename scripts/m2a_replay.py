#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m2a_replay.py —— T2 可复现回放基建的验证器（M2a 交付判据）

判据：同一回放脚本 (seed, fixedDt, timeScale, targetFps, duration) 连跑两遍，
在相对帧 [100,110,...,210] 采样 Mover 位置序列，逐点比对：
  - rel 点列必须完全一致（帧索引对齐的定义）
  - mover x/z 与 replayT 在容差内一致（行为确定性）
输出 runs/m2a_alignment_report.json。

前置：常驻 GUI Editor（G16）、Scene_F-CPU-01 存在、ReplayDriver.cs 已编译。
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "fixtures" / "upi-fixture"
PROBEDIR = ROOT / "runs" / "_probe"
FRAMES = PROBEDIR / "rp_frames.txt"
CTL = PROBEDIR / "rp_cmd.txt"
RES = PROBEDIR / "rp_res.txt"
HB = PROBEDIR / "rp_hb.txt"
OUT = ROOT / "runs" / "m2a_alignment_report.json"
UNITY = "unity"

# 回放脚本（两遍必须逐字节相同，否则哈希不同→G7 拦截）
SCRIPT6 = dict(seed=20261108, dt_num=1, dt_den=60, ts_millis=1000,
               target_fps=60, duration_ms=120000)
ARM = dict(at_rel=100, stride=10, count=12)
TOL = 1e-4   # 位置容差（米）；帧索引时基下理论应为 0


def cli(group_leaf, extra=None, timeout=120):
    args = [UNITY, "command", group_leaf, "--project-path", str(PROJ),
            "--format", "json", "--no-banner", "--timeout", str(timeout)]
    if extra:
        args += extra
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 60)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "errors": [{"message": (p.stdout or p.stderr)[:300]}]}


def ctl_write(cmd: str, wait_s: float = 6.0):
    """向常驻驱动器发文件命令并等回执（10Hz 检查节流 ⇒ 上限 ~0.1s+ε）。"""
    PROBEDIR.mkdir(parents=True, exist_ok=True)
    res_old = RES.read_text() if RES.exists() else None
    tmp = CTL.with_suffix(".tmp")
    tmp.write_text(cmd)
    shutil.move(str(tmp), str(CTL))
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(0.4)
        if RES.exists() and RES.read_text() != res_old:
            return RES.read_text()
    return "(回执超时)"


def heartbeat_fresh(max_age_s=3.0) -> bool:
    if not HB.exists():
        return False
    return (time.time() - HB.stat().st_mtime) < max_age_s


def read_frames() -> list[dict]:
    if not FRAMES.exists():
        return []
    rows = []
    for ln in FRAMES.read_text().strip().splitlines():
        d = {}
        for kv in ln.split():
            k, _, v = kv.partition("=")
            d[k] = v
        rows.append(d)
    return rows


def one_replay(tag: str, report: dict) -> list[dict]:
    print(f"[{tag}] 打开 F-CPU-01 场景", flush=True)
    r = cli("scene open", ["--path", "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity"])
    if not r.get("success"):
        # 播放模式占用时先停
        cli("editor.playmode stop")
        time.sleep(4)
        r = cli("scene open", ["--path", "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity"])
    report.setdefault(tag, {})["scene_open"] = bool(r.get("success"))

    s = SCRIPT6
    load = "load:%d:%d:%d:%d:%d:%d" % (s["seed"], s["dt_num"], s["dt_den"],
                                       s["ts_millis"], s["target_fps"], s["duration_ms"])
    res1 = ctl_write(load)
    res2 = ctl_write("arm:%d:%d:%d" % (ARM["at_rel"], ARM["stride"], ARM["count"]))
    a = {"load_ack": res1, "arm_ack": res2}
    print(f"[{tag}] load -> {res1}", flush=True)
    print(f"[{tag}] arm  -> {res2}", flush=True)
    report[tag]["acks"] = a

    r = cli("editor.playmode start")
    report[tag]["play_start"] = bool(r.get("success"))
    # 采样窗口：相对帧 100..210，60fps 下 ~1.8s + 进入 Play/域重载余量
    deadline = time.time() + 45
    rows = []
    while time.time() < deadline:
        time.sleep(2)
        rows = read_frames()
        if len(rows) >= ARM["count"]:
            break
    report[tag]["n_samples"] = len(rows)
    report[tag]["frames"] = rows

    cli("editor.playmode stop")
    time.sleep(4)
    return rows


def compare(r1: list[dict], r2: list[dict]) -> dict:
    out = {"n1": len(r1), "n2": len(r2), "max_pos_diff": None,
           "max_replayT_diff": None, "rel_equal": False,
           "pointwise": [], "verdict": "insufficient_evidence"}
    if len(r1) < ARM["count"] or len(r2) < ARM["count"]:
        out["why"] = f"采样点不足（{len(r1)}/{len(r2)} < {ARM['count']}）"
        return out
    rels1 = [int(x["rel"]) for x in r1]
    rels2 = [int(x["rel"]) for x in r2]
    out["rel_equal"] = rels1 == rels2
    md = mt = 0.0
    for a, b in zip(r1, r2):
        if a.get("mover") == "nan" or b.get("mover") == "nan":
            out["why"] = "Mover 缺失（场景未含驱动物体？）"
            return out
        ax, az = map(float, a["mover"].split(","))
        bx, bz = map(float, b["mover"].split(","))
        d = max(abs(ax - bx), abs(az - bz))
        md = max(md, d)
        mt = max(mt, abs(float(a["replayT"]) - float(b["replayT"])))
        out["pointwise"].append({"rel": int(a["rel"]), "pos_diff": round(d, 8),
                                 "replayT_diff": round(float(a["replayT"]) - float(b["replayT"]), 8)})
    out["max_pos_diff"] = md
    out["max_replayT_diff"] = mt
    if out["rel_equal"] and md <= TOL:
        out["verdict"] = "aligned"
    elif out["rel_equal"]:
        out["verdict"] = "frame_aligned_behavior_drift"
    else:
        out["verdict"] = "frame_misaligned"
    return out


def main() -> int:
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "script": SCRIPT6, "arm": ARM, "tol": TOL}

    if not heartbeat_fresh():
        print("!! 回放驱动器心跳不新鲜——先重启编辑器让新代码生效（I9）")
        report["final"] = "driver_not_alive"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    FRAMES.unlink(missing_ok=True)
    r1 = one_replay("run1", report)
    FRAMES.unlink(missing_ok=True)
    time.sleep(2)
    r2 = one_replay("run2", report)

    cmp_ = compare(r1, r2)
    report["comparison"] = cmp_
    report["final"] = cmp_["verdict"]
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n==== T2 对齐判定 ====")
    print("rel 点列一致:", cmp_["rel_equal"], f"（{cmp_['n1']} vs {cmp_['n2']} 点）")
    if cmp_["max_pos_diff"] is not None:
        print(f"位置最大偏差: {cmp_['max_pos_diff']:.3e} m（容差 {TOL:.0e}）")
        print(f"replayT 最大偏差: {cmp_['max_replayT_diff']:.3e} s")
    print("判定:", cmp_["verdict"], cmp_.get("why", ""))
    print("报告:", OUT)
    return 0 if cmp_["verdict"] == "aligned" else 1


if __name__ == "__main__":
    raise SystemExit(main())
