"""scripts/run_exp5.py —— 渲染计数器注入通路终局判定的一键驱动（exp5 正式件）。

三轮演进见 probe/exp5_render_counter_handle.cs 头注（全部实测，非推测）。
本驱动做的事：
  1. 打开 F-GPU-01 场景（batches/draw 有真实活动，可与外部轮询对表）；
  2. 进 Play → 注入探针（handle-Default 与 StartImmediately 双构造）；
  3. **并行**：注入侧每帧落 ISO 时间戳行；驱动侧 ~1Hz 轮询
     `get_performance`（渲染计数唯一已验证源，G16）也落 ISO 时间戳行；
  4. 事后按最近时间对齐（±1.5s 内），判据 = 同数量级（比值 0.2~5），
     不是"非零"——0 可能是合法的（该刻确实没绘制），量级对不上才是坏的。
     这一步同时是"探针单源 vs 轮询"的第一次小双跑等价性验证（#21 迁移判据的前置）。

⚠️ eval_file 已弃用（R2 注入返回 warnings 实测），走 `script eval --file`。
收尾：停 Play（域重载自动卸回调，I9 实测），不卸会污染下一轮采样。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"
PROBE = ROOT / "runs" / "_probe"
SRC = ROOT / "probe" / "exp5_render_counter_handle.cs"
PROBE_OUT = PROBE / "exp5_probe.jsonl"
POLL_OUT = PROBE / "exp5_poll.jsonl"
SCENE = "Assets/UpiFixtures/Scenes/Scene_F-GPU-01.unity"


def unity(*args: str, timeout: int = 150) -> dict:
    r = subprocess.run(["unity", *args, "--format", "json", "--no-banner",
                        "--non-interactive"],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "errors": [{"message": (r.stdout or r.stderr)[:200]}]}


def cmd(name: str, *extra: str, timeout: int = 150) -> dict:
    return unity("command", name, "--project-path", str(PROJECT), *extra, timeout=timeout)


def parse_iso(s: str) -> datetime:
    # 两侧统一 UTC（R3 实测教训：探针侧 DateTime.Now 本地 ISO 带 +08:00、
    # 驱动侧 naive ⇒ aware-naive 相减 TypeError）。Z 后缀 fromisoformat 旧版不认。
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> int:
    PROBE.mkdir(parents=True, exist_ok=True)
    for f in (PROBE_OUT, POLL_OUT):
        f.unlink(missing_ok=True)

    st = (cmd("editor status").get("data") or {}).get("result") or {}
    if str(st.get("playMode")) != "stopped":
        print("!! 非 stopped 现场，中止（不强停别人的 Play）")
        return 2

    print("[0] 打开 F-GPU-01")
    r = cmd("scene open", "--path", SCENE, timeout=120)
    if not r.get("success"):
        print("!! 场景打开失败:", str(r.get("errors"))[:200]); return 1

    print("[1] 进 Play")
    cmd("editor.playmode start", timeout=90)
    ok = False
    for _ in range(10):
        time.sleep(4)
        st = (cmd("editor status").get("data") or {}).get("result") or {}
        if str(st.get("playMode")) == "playing":
            ok = True
            break
    if not ok:
        print("!! 60s 未进 Play，中止"); cmd("editor.playmode stop"); return 1

    print("[2] 注入探针（script eval --file，eval_file 已弃用）")
    dst = PROJECT / "exp5_render_counter_handle.cs"
    shutil.copy(SRC, dst)
    try:
        r = cmd("script eval", "--file", dst.name, timeout=300)
    finally:
        dst.unlink(missing_ok=True)
    if not r.get("success"):
        print("!! 注入失败:", str(r.get("errors"))[:300])
        cmd("editor.playmode stop")
        return 1
    print(str((r.get("data") or {}).get("result", ""))[:700].replace("\\r", "\r").replace("\\n", "\n"))

    print("[3] 并行轮询 get_performance ~1Hz，直到探针 DONE 或 90s")
    t0 = time.time()
    with POLL_OUT.open("w", encoding="utf-8") as pf:
        while time.time() - t0 < 90:
            if PROBE_OUT.exists():
                try:
                    if "DONE" in PROBE_OUT.read_text(encoding="utf-8")[-60:]:
                        break
                except OSError:
                    pass
            rr = cmd("get_performance", timeout=60)
            res = (rr.get("data") or {}).get("result") or {}
            rd = (res.get("render") or {}) if isinstance(res, dict) else {}
            pf.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                 "drawCalls": rd.get("drawCalls"),
                                 "batches": rd.get("batches"),
                                 "setPassCalls": rd.get("setPassCalls"),
                                 "triangles": rd.get("triangles")}) + "\n")
            pf.flush()
            time.sleep(1.0)

    print("[4] 停 Play（域重载自动卸回调）")
    cmd("editor.playmode stop", timeout=60)
    time.sleep(4)

    # ── 对齐判定：注入行 × 轮询行，最近时间 ±1.5s，比值 0.2~5 记"同数量级" ──
    pairs = [("Default|Draw Calls Count", "drawCalls"),
             ("Default|SetPass Calls Count", "setPassCalls"),
             ("Default|Batches Count", "batches"),
             ("Default|Triangles Count", "triangles"),
             ("StartImmediately|Draw Calls Count", "drawCalls"),
             ("StartImmediately|SetPass Calls Count", "setPassCalls")]
    pr = [json.loads(l) for l in PROBE_OUT.read_text(encoding="utf-8").splitlines()
          if l.startswith("{")] if PROBE_OUT.exists() else []
    po = [json.loads(l) for l in POLL_OUT.read_text(encoding="utf-8").splitlines()] \
        if POLL_OUT.exists() else []
    print(f"\n===== 对齐判定（探针 {len(pr)} 帧 × 轮询 {len(po)} 次）=====")
    n_ok = n_bad = 0
    for key, pollk in pairs:
        agree = disagree = zero = 0
        for a in pr:
            v = (a.get("vals") or {}).get(key)
            if v is None or v < 0:
                continue
            ta = parse_iso(a["ts"])
            b = min(po, key=lambda q: abs((parse_iso(q["ts"]) - ta).total_seconds()),
                    default=None)
            if b is None or abs((parse_iso(b["ts"]) - ta).total_seconds()) > 1.5:
                continue
            pv = b.get(pollk)
            if pv is None:
                continue
            if v == 0 and pv == 0:
                zero += 1
            elif v > 0 and pv > 0 and 0.2 <= v / pv <= 5:
                agree += 1
            else:
                disagree += 1
        tot = agree + disagree + zero
        verdict = ("可替代" if disagree == 0 and tot >= 10 and agree >= tot * 0.6 else
                   "部分失真" if disagree and agree else "不可用")
        n_ok += 1 if verdict == "可替代" else 0
        n_bad += 1 if verdict != "可替代" else 0
        print(f"  {key:44s} 同量级={agree} 双零={zero} 失真={disagree} ⇒ {verdict}"
              f"（n={tot}）")
    print(f"\n结论计数: 可替代 {n_ok} 项 / 其余 {n_bad} 项")
    print("诚实边界: F-GPU-01 常态负载、单场景单档位、±1.5s 对齐误差；"
          "等价性充分验证需跨夹具双跑（#21 迁移判据），本件是第一小步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
