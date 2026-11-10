"""scripts/run_exp6_equiv.py —— #27 真机补验：v3.1 注入侧 × 外部轮询侧对表。

要回答的两个悬案（都关系到 G16/G20 怎么改判，见 sampler_v3.cs v3.1 头注）：
  ① 渲染三项（draw/setpass/triangles）+ 内存两项在**新 Targets 表**下与轮询
     同数量级吗（exp5 用的是临时片段，本版是常驻件改名后的首次复验）；
  ② batches 分项恒 0 之谜：同一场景同一时刻，轮询 render.batches 与注入
     Dynamic/Instanced/Static Batches Count 是不是**一起**为 0（合法零）
     还是注入侧单点失效（口径断裂）。两侧同零 ⇒ 夹具 SRP 不合批实锤，
     等价性验证需要真实合批场景（记入未决，G20 续列）。

判据沿用 exp5：±1.5s 最近时间对齐，比值 0.2~5 记同数量级；0 可能是合法的。
纪律：现场非 stopped 不强停别人的 Play；注入走 script eval --file（eval_file
已弃用）；停 Play 靠域重载卸回调；失焦停帧 ⇒ 帧号不推进直接中止如实报。
信封读取一律走 run_unity（normalize_envelope 唯一入口，别再手搓 json.loads）。
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from upi.tooling.unity_cli import run_unity  # noqa: E402

PROJECT = ROOT / "fixtures" / "upi-fixture"
SRC = ROOT / "probe" / "sampler_v3.cs"
PROBE_DIR = ROOT / "runs" / "_probe"
DATA = PROBE_DIR / "data.json"
CMD = PROBE_DIR / "cmd.txt"
HB = PROBE_DIR / "heartbeat.txt"
POLL_OUT = PROBE_DIR / "exp6_poll.jsonl"
SCENE = "Assets/UpiFixtures/Scenes/Scene_F-GPU-01.unity"
N_WINDOW = 300


def cmd(name: str, *extra: str, timeout: int = 300):
    return run_unity(["command", name, "--project-path", str(PROJECT), *extra],
                     timeout_s=timeout)


def editor_field(env: dict, key: str):
    # 归一后 data.result = CLI 裸载荷本体（实测：{"status":"ready","playMode":...}
    # 平铺）。兼容个别版本包一层 {"status":{...}} 的形态。
    res = (env.get("data") or {}).get("result") or {}
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except json.JSONDecodeError:
            return None
    if not isinstance(res, dict):
        return None
    if key in res and not isinstance(res[key], dict):
        return res[key]
    st = res.get("status")
    return st.get(key) if isinstance(st, dict) else None


def main() -> int:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    DATA.unlink(missing_ok=True)

    st0 = cmd("editor status", timeout=90)
    if not st0.success:
        print("!! 夹具编辑器不可达:", str(st0.errors)[:200]); return 1
    pm = editor_field(st0.model_dump(), "playMode")
    if pm != "stopped":
        print(f"!! 现场 playMode={pm} 非 stopped，中止（不强停别人的 Play）"); return 2

    print("[0] 打开 F-GPU-01")
    r = cmd("scene", "open", "--path", SCENE, timeout=120)
    if not r.success:
        print("!! 场景打开失败:", str(r.errors)[:250]); return 1

    print("[1] 进 Play")
    cmd("editor.playmode", "start", timeout=90)
    ok = False
    for _ in range(12):
        time.sleep(4)
        if editor_field(cmd("editor status", timeout=60).model_dump(), "playMode") == "playing":
            ok = True; break
    if not ok:
        print("!! 48s 未进 Play，中止"); cmd("editor.playmode", "stop", timeout=60); return 1

    print("[2] 注入 v3.1（script eval --file）")
    dst = PROJECT / SRC.name
    shutil.copy(SRC, dst)
    try:
        r = cmd("script", "eval", "--file", dst.name, timeout=300)
    finally:
        dst.unlink(missing_ok=True)
    if not r.success:
        print("!! 注入失败:", str(r.errors)[:300])
        cmd("editor.playmode", "stop", timeout=60); return 1
    res = (r.data or {}).get("result") or {}
    out = res.get("output") if isinstance(res, dict) else None
    line = (out or res.get("result") if isinstance(res, dict) else res) or ""
    print("   注入回执:", str(line).replace("\\r", "\r").replace("\\n", "\n")[:400])

    print("[3] 触发窗口并并行轮询 get_performance ~1Hz")
    CMD.write_text(f"start:{N_WINDOW}", encoding="utf-8")
    t0 = time.time()
    polls = []
    while time.time() - t0 < 150:
        if DATA.exists():
            try:
                d = json.loads(DATA.read_text(encoding="utf-8"))
                if d.get("why") in ("window_done", "stop"):
                    break
            except (json.JSONDecodeError, OSError):
                pass
        rr = cmd("get_performance", timeout=60)
        rd = (((rr.data or {}).get("result") or {})).get("render") or {}
        mem = (((rr.data or {}).get("result") or {})).get("memory") or {}
        polls.append({"ts": datetime.now(timezone.utc).isoformat(),
                      "drawCalls": rd.get("drawCalls"), "batches": rd.get("batches"),
                      "setPassCalls": rd.get("setPassCalls"), "triangles": rd.get("triangles"),
                      "vertices": rd.get("vertices"),
                      "totalUsedBytes": mem.get("totalAllocatedBytes")})
        time.sleep(1.0)
    else:
        print("!! 150s 未落数据（疑失焦停帧/注入未活）; heartbeat 文件年龄(s)="
              f"{None if not HB.exists() else round(time.time() - HB.stat().st_mtime, 1)}")
        cmd("editor.playmode", "stop", timeout=60)
        return 1

    print("[4] 停 Play（域重载卸回调）")
    cmd("editor.playmode", "stop", timeout=60)

    POLL_OUT.write_text("\n".join(json.dumps(p) for p in polls), encoding="utf-8")

    # ── 对表判定 ──
    ser = d.get("series") or {}
    print(f"\n===== 注入侧系列（n={d.get('n')}, skipped={d.get('skipped')}）=====")
    rows = []
    for key, s in ser.items():
        if not s:
            rows.append((key, 0, 0, 0, "空序列")); continue
        med = statistics.median(s)
        nz = sum(1 for v in s if v != 0)
        rows.append((key, med, nz, len(s), "有数" if nz else "全零"))
    for key, med, nz, n, tag in rows:
        print(f"  {key:32s} 中位={med:>12.0f} 非零={nz}/{n} {tag}")

    # 与轮询中位对表（数量级判据）
    def pol_med(k):
        vals = [p[k] for p in polls if isinstance(p.get(k), (int, float))]
        return statistics.median(vals) if vals else None

    pairs = [("Render/DrawCalls", "drawCalls"), ("Render/SetPassCalls", "setPassCalls"),
             ("Render/Triangles", "triangles"), ("Render/Vertices", "vertices"),
             ("Memory/TotalUsedBytes", "totalUsedBytes")]
    print("\n===== 两侧中位对表（比值 0.2~5 记同数量级）=====")
    for inj_k, pol_k in pairs:
        iv = next((m for (k, m, *_ ) in rows if k == inj_k), None)
        pv = pol_med(pol_k)
        if iv is None or pv in (None, 0):
            print(f"  {inj_k:28s} vs {pol_k:18s} ⇒ 不可判（一侧无数）"); continue
        ratio = iv / pv if pv else None
        okq = ratio is not None and 0.2 <= ratio <= 5
        print(f"  {inj_k:28s} vs {pol_k:18s} 注入={iv:.0f} 轮询={pv:.0f} "
              f"比值={ratio:.2f} ⇒ {'同数量级' if okq else '量级不符'}")

    # batches 悬案专判
    print("\n===== batches 悬案 =====")
    pv_b = pol_med("batches")
    print(f"  轮询 batches 中位 = {pv_b}")
    for bk in ("Render/DynBatches", "Render/InstBatches", "Render/StaticBatches"):
        m = next((mm for (k, mm, *_ ) in rows if k == bk), None)
        print(f"  注入 {bk:26s} 中位 = {m}")
    print("  判读: 两侧同零 ⇒ 场景确无合批（合法零，等价性未决需合批场景）；"
          "轮询非零而注入全零 ⇒ 分项口径断裂，如实报。")

    print(f"\n留痕: {DATA.name} / {POLL_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
