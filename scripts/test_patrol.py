#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自主巡检循环离线测试（全边界替身：不采样、不碰 Editor、不烧积分）。

被测行为约定（patrol.py 模块头 B1~B6）：
 ① 发作通路：jsonl 缺失/心跳缺失/心跳超龄 ⇒ 盲区声明，不冒充"无发作"；
    新鲜心跳 + 合规越界样本 ⇒ findings；增量偏移消费后同一条不重复报
 ② 水通路：无 CLEAN ⇒ 拒判（§8.1）；重采失败 ⇒ 盲区；合成超阈束 ⇒ 检出
    且 magnitude 取 issue 侧（键名错会被抓）；正常束 ⇒ 零检出
 ③ patrol_once：静默轮也落 patrol_runs.jsonl（B1）；检出且 auto_fix ⇒ 唤起；
    唤起后冷却期内不重复（B5）；非 auto_fix 夹具只排队不唤起（B3）
 ④ 配置：缺 pipeline 段 ⇒ enabled=False（不猜默认）；interval<60 ⇒ 拒收
 ⑤ 入口分派：未知命令非零退出；monitor 未启用 ⇒ 退出码 2

真实标定文件与检测器注册表直接引用（测的就是真判据链在巡检封装下的行为）。
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import upi.agents.patrol as P      # noqa: E402

TMP = ROOT / "runs" / "_patrol_test"
PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}")


def win_metrics(vals):
    """构造 m1_multiwin 束的窗口形状（median 由 stats_from_windows 消费）。"""
    return {"windows": [{"metrics": {k: {"median": v[i]} for k, v in vals.items()},
                         "a2": {"gc_median": vals.get("gc", [0]*4)[i], "gc_n": 60}}
                        for i in range(4)]}


def synth_bundle(clean_hi=False, cpu_defect=False):
    """合成束：CALDIR 无关，路径给 bundle_stats 用。"""
    base = {"cpuFrameTimeMs": 1.9, "drawCalls": 40.0, "batches": 12.0,
            "setPassCalls": 9.0, "triangles": 2100.0, "vertices": 6000.0,
            "monoUsedBytes": 100e6, "monoHeapBytes": 2.1e9,
            "totalAllocatedBytes": 4.3e9, "totalReservedBytes": 7.7e9,
            "gpuFrameTimeMs": 1.6, "cpuMainThreadFrameTimeMs": 0.9,
            "available": 1.0}
    clean = {k: [v] * 4 for k, v in base.items()}
    clean["gc"] = [9000.0] * 4
    cpu = dict(clean)
    if cpu_defect:
        # 远超标定阈（gc min_magnitude=20000, dev_ratio=1.5）
        cpu = dict(clean)
        cpu["gc"] = [90000.0] * 4
    fx = {"CLEAN": win_metrics(clean)}
    # 请求采样的夹具必须恒在束里（缺采=盲区，正是 B7 闸要抓的形状）：
    # 上一版只在缺陷时放 F-CPU-01，正常束缺它会被闸拦下——测试自己先撞。
    fx["F-CPU-01"] = win_metrics(cpu)
    return {"session": "full", "windows_target": 4,
            "protocol": {"warmup": 8, "window_polls": 20, "poll_interval_s": 1.0},
            "fixtures": fx}


def make_sample_jsonl(path: Path, seed=1):
    """合规越界样本行（DET-LOAD-FRAMESPIKE 线=33.333ms，取 65.5ms）。"""
    row = {"frame_idx": 1234, "rel": 300, "metric": "frame_time_ms",
           "value": 65.4976, "threshold_abs": 33.333, "deviation_ratio": 1.965,
           "ring_before": [2.0] * 10, "ring_after": [2.0] * 14,
           "gc_ring_before": [500.0] * 10, "gc_ring_after": [500.0] * 14,
           "a4_window": [{"frame_idx": 1230 + i, "cpu_frame_time_ms": 2.0,
                          "gpu_frame_time_ms": 1.5, "complete_ts": 1}
                         for i in range(14)],
           "data_tier": "A2", "protocol_fingerprint": "fp-test",
           "replay_seed": seed}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    P.PATROL_LOG = TMP / "patrol_runs.jsonl"
    P.STATE_FP = TMP / "patrol_state.json"

    print("== ① 发作通路：盲区声明与真检出 ==")
    cfg = {"episode": {"jsonl": "runs/_patrol_test/nope.jsonl",
                       "heartbeat": "runs/_patrol_test/no_hb.txt",
                       "heartbeat_max_age_s": 12}}
    r = P.patrol_episode(cfg)
    check("jsonl 缺失 ⇒ 盲区（不是'无发作'）",
          r["blindspot"] and "未装" in r["blindspot"] and not r["findings"])

    jb = TMP / "pb_breach.jsonl"
    make_sample_jsonl(jb)
    cfg2 = {"episode": {"jsonl": str(jb),
                        "heartbeat": str(TMP / "hb.txt"),
                        "heartbeat_max_age_s": 12}}
    r = P.patrol_episode(cfg2)
    check("心跳文件缺失 ⇒ 盲区", r["blindspot"] and "心跳" in r["blindspot"])

    import os
    (TMP / "hb.txt").write_text("")   # 先建文件，再把 mtime 拨旧（实现看 mtime）
    os.utime(TMP / "hb.txt", (time.time() - 60, time.time() - 60))
    r = P.patrol_episode(cfg2)
    check("心跳超龄 ⇒ 盲区且写明非播放态口径（G19 不虚报）",
          r["blindspot"] and "非播放态" in r["blindspot"])

    (TMP / "hb.txt").write_text(str(time.time()))
    r = P.patrol_episode(cfg2)
    f0 = r["findings"][0] if r["findings"] else {}
    check("新鲜心跳 + 越线样本 ⇒ 发作 finding",
          len(r["findings"]) == 1 and f0.get("detector") == "DET-LOAD-FRAMESPIKE"
          and f0.get("ptype") == "episode")
    check("state 记录偏移量（增量消费）",
          P._load_state().get("episode_offset", 0) > 0)
    r2 = P.patrol_episode(cfg2)
    check("同一条不重复报（n_new_samples=0）",
          r2.get("n_new_samples") == 0 and not r2["findings"])
    # 不可复现样本（seed=-1）允许入账但禁进循环 → findings 为空（T2/I2）
    make_sample_jsonl(jb, seed=-1)
    P._save_state({"episode_offset": 0})
    r3 = P.patrol_episode(cfg2)
    check("replay_seed=-1 样本不进 findings（不可复现禁进修复循环）",
          r3["n_samples"] == 1 and not r3["findings"])

    print("== ② 水通路：CLEAN 前置与真判据链 ==")
    check("夹具不含 CLEAN ⇒ 拒判（§8.1）",
          "CLEAN" in (P.patrol_waterline({"fixtures": ["F-CPU-01"]},
                                         TMP / "w1")["blindspot"] or ""))

    # 重采失败：mock subprocess 造个 rc≠0 且无输出
    import subprocess as sp
    real_run = sp.run

    class FakeP:
        returncode = 3
        stdout = "boom"
        stderr = ""
    sp.run = lambda *a, **k: FakeP()
    try:
        r = P.patrol_waterline({"fixtures": ["CLEAN", "F-CPU-01"],
                                "windows": 4, "waterline_timeout_s": 5},
                               TMP / "w2")
    finally:
        sp.run = real_run
    check("重采失败 ⇒ 盲区（rc 可见）",
          r["blindspot"] and "rc=3" in r["blindspot"])

    # 合成超阈束：把 bundle 落盘后跳过重采（mock sp.run 写文件）
    def make_fake_run(defect):
        def _f(*a, **k):
            (TMP / "w3" / "multiwin_full.json").write_text(
                json.dumps(synth_bundle(cpu_defect=defect)), encoding="utf-8")
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})
        return _f
    (TMP / "w3").mkdir(parents=True)
    sp.run = make_fake_run(True)
    try:
        r = P.patrol_waterline({"fixtures": ["CLEAN", "F-CPU-01"],
                                "windows": 4, "waterline_timeout_s": 5},
                               TMP / "w3")
    finally:
        sp.run = real_run
    f = r["findings"][0] if r["findings"] else {}
    check("超阈缺陷束 ⇒ 检出且 magnitude 非空（键名回归）",
          f.get("detector") == "DET-CPU-GC-FRAMEALLOC"
          and f.get("magnitude") not in (None, 0))
    sp.run = make_fake_run(False)
    try:
        r = P.patrol_waterline({"fixtures": ["CLEAN", "F-CPU-01"],
                                "windows": 4, "waterline_timeout_s": 5},
                               TMP / "w3")
    finally:
        sp.run = real_run
    check("正常束 ⇒ 零检出", not r["findings"] and not r["blindspot"])

    # 静默假象闸：缺陷夹具窗口为空（采集失败的束形状）⇒ 盲区，不冒充零检出
    def make_fake_run_broken(*a, **k):
        doc = synth_bundle()
        doc["fixtures"]["F-CPU-01"] = {"windows": [], "error": "编辑器未就绪"}
        (TMP / "w4").mkdir(parents=True, exist_ok=True)
        (TMP / "w4" / "multiwin_full.json").write_text(
            json.dumps(doc), encoding="utf-8")
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})
    _saved = sp.run
    sp.run = lambda *a, **k: make_fake_run_broken()
    try:
        rb = P.patrol_waterline({"fixtures": ["CLEAN", "F-CPU-01"],
                                 "windows": 4, "waterline_timeout_s": 5},
                                TMP / "w4")
    finally:
        sp.run = _saved
    check("夹具 0 窗口的束 ⇒ 盲区（采集失败≠未检出，禁产静默轮）",
          rb["blindspot"] and "有效前提" in rb["blindspot"] and not rb["findings"])

    print("== ③ patrol_once：留痕 / 唤起 / 冷却 / 排队 ==")
    woken = []

    def fake_wake(fx_item, cfg_mon):
        woken.append(fx_item["fixture"])
        return {"run_id": "x", "route": "needs_human",
                "approval": "runs/x/approvals/a/request.md"}
    real_wake = P.wake_state_machine
    P.wake_state_machine = fake_wake
    # 直接喂两条通路的替身（patrol_once 只消费其返回形状）
    real_ep, real_wl = P.patrol_episode, P.patrol_waterline
    P.patrol_episode = lambda c: {"channel": "episode", "findings": [],
                                  "blindspot": None}
    P.patrol_waterline = lambda c, w: {
        "channel": "waterline", "blindspot": None,
        "findings": [{"kind": "issue", "ptype": "waterline",
                      "fixture": "F-CPU-01", "detector": "DET-CPU-GC-FRAMEALLOC",
                      "metric": "gc_alloc_per_frame", "magnitude": 81000.0},
                     {"kind": "issue", "ptype": "waterline",
                      "fixture": "F-GPU-01", "detector": "DET-GPU-BATCHBREAK",
                      "metric": "drawCalls", "magnitude": 62.0}]}
    cfgm = {"fixtures": ["CLEAN"], "auto_fix": {"fixtures": ["F-CPU-01"]},
            "cooldown_s": 1800, "episode": {}}
    rec = P.patrol_once(cfgm)
    check("auto_fix 夹具唤起，非 auto_fix（F-GPU-01）只排队不唤起（B3）",
          woken == ["F-CPU-01"] and len(rec["waterline"]["findings"]) == 2)
    check("唤起记录 + 冷却时间戳落 state",
          rec["wake"]["F-CPU-01"]["woken"]
          and "F-CPU-01" in P._load_state().get("last_wake", {}))
    woken.clear()
    rec2 = P.patrol_once(cfgm)
    check("冷却期内不重复唤起（B5）",
          woken == [] and rec2["wake"]["F-CPU-01"]["why"] == "cooldown")
    # 静默轮留痕（B1）
    P.patrol_waterline = lambda c, w: {"channel": "waterline", "findings": [],
                                       "blindspot": None}
    rec3 = P.patrol_once(cfgm)
    lines = P.PATROL_LOG.read_text(encoding="utf-8").splitlines()
    check("静默轮也落 patrol_runs.jsonl（B1：跑了≠睡着）",
          rec3["quiet"] and len(lines) == 3)
    # 盲区轮不算静默（B7：采不到 ≠ 没有异常）
    P.patrol_waterline = lambda c, w: {
        "channel": "waterline", "findings": [],
        "blindspot": "采样有效前提不成立：F-CPU-01 采集失败"}
    rec4 = P.patrol_once(cfgm)
    check("盲区轮不产静默结论（未评估≠零异常）",
          rec4["quiet"] is False and bool(rec4["blindspots"]))
    P.wake_state_machine, P.patrol_episode, P.patrol_waterline = \
        real_wake, real_ep, real_wl

    print("== ④ 配置：缺段不启用 / 非法拒收 ==")
    from upi.config.settings import LlmConfig, MonitorConfig
    check("缺 pipeline 段 ⇒ enabled=False（不猜默认）",
          LlmConfig.model_validate({
              "schema_version": 1, "providers": {},
              "agent_model_parameters": {"defaults": {}}}).monitor().enabled is False)
    try:
        MonitorConfig(interval_s=30)
        ok = False
    except Exception:  # noqa: BLE001
        ok = True
    check("interval<60s ⇒ 构造拒收（成本护栏入 schema）", ok)
    check("默认 enabled=False", MonitorConfig().enabled is False)

    print("== ⑤ 入口分派 ==")
    import upi.__main__ as M
    check("未知命令返回 2", M.main(["bogus"]) == 2)
    check("无参数打印用法返回 0", M.main([]) == 0)

    print(f"\n==== 巡检离线测试: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    print("边界声明：唤起状态机用替身（真唤起=真采样 25 分钟）；水通路判据是"
          "真 compare+真标定文件，只是束数据为合成。patrol 的真实端到端"
          "并入实测轮。")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
