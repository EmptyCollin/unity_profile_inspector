"""scripts/run_exp7_batches.py —— batches 悬案裁决（#27，**本件结论=无效，留件防重蹈**）。

实测作废记录（2026-11-11 两轮跑）：
  ① `QualitySettings.enableDynamicBatching` 在 Unity 6000 **不存在**（eval 编译期
     CS0117，直查信封 diagnostics 实证）⇒ 翻转语句从未执行过，"A/B 两窗均无变化"
     是**假裁决**（压根没翻转）。副作用核对过：语句编译失败即未运行，不存在
     需要还原的状态；ProjectSettings 磁盘未脏（git status 干净）。
  ② B 窗 drawCalls 中位=0（A 窗 172）——窗口期未正常出帧（疑失焦停帧），
     再次印证采样窗必须带帧号推进守卫。
  ③ 顺带撞出取层错误：`unity command script eval` 的返回在**内层 result**，
     eval 级 success=true 不代表代码执行成功（内层还有自己的 success/errorCode）
     ——只看外层 success 会把编译失败当成"返回 None 的活代码"。本件 ev() 现版
     取内层，调用方必须查 diagnostics（见 ev_ok 断言）。

悬案的**有效**实证部分（不靠本件）：句柄全量表（counter_cat_full.txt 5298 行）
证实 Render 类目**没有**汇总 "Batches Count"，只有 Dynamic/Instanced/Static 三个
分项 ⇒ 轮询 render.batches 与注入分项**不同口径**；SRP 夹具场景两侧同零 ⇒ 等价性
无从对表（零对零无信息量，exp5 规矩）。定论：batches 项记 G20 续列，解锁路径=
真实非零合批场景（GPU instancing 同材质多实例 / 静态合批 / 内置管线对照），
dynamic batching 翻转路线在 Unity 6000 已死，不许再试。

悬案：exp6 里 F-GPU-01 两侧 batches 同零，分不清"合法零（SRP 不合批）"还是
"分项口径断裂"。句柄表已证实 Render 无汇总 Batches Count，只有 Dyn/Inst/Static
三分项。本件把 QualitySettings.enableDynamicBatching 从夹具默认(false)翻到 true，
采翻转前(A)与翻转后(B)两个逐帧窗，看注入侧 DynBatches 分项动不动、轮询 batches
动不动：
  - B 段任一侧出现非零 ⇒ 分项/轮询随合批开关同向动 ⇒ 口径吻合（悬案解）；
  - A、B 两侧仍全零 ⇒ SRP 下 dynamic batching 不入这些口径 ⇒ 等价性对 batches
    项**结构性无从对表**，非"注入坏了"。据实记入 G20（解锁需非 SRP 或强制合批
    场景），不假装测到了。

纪律同 exp6：现场非 stopped 不强停；注入走 script eval；停 Play 卸回调；
翻转只改 QualitySettings 内存态（不写 quality 资产、不脏工程），B 窗采完翻回。
信封读取一律 run_unity。每窗采完立刻把 data.json 另存，避免下窗清空覆盖。
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from upi.tooling.unity_cli import run_unity  # noqa: E402

PROJECT = ROOT / "fixtures" / "upi-fixture"
SRC = ROOT / "probe" / "sampler_v3.cs"
PROBE_DIR = ROOT / "runs" / "_probe"
DATA = PROBE_DIR / "data.json"
CMD = PROBE_DIR / "cmd.txt"
SCENE = "Assets/UpiFixtures/Scenes/Scene_F-GPU-01.unity"
WIN = 240


def cmd(name: str, *extra: str, timeout: int = 300):
    return run_unity(["command", name, "--project-path", str(PROJECT), *extra],
                     timeout_s=timeout)


def field(env: dict, key: str):
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


def ev(code: str, timeout: int = 220) -> tuple[bool, str]:
    """返回 (代码真执行了吗, 输出)。⚠️ 必须查内层：外层 success=true 只表示
    eval 通道活着，代码编译失败时内层 success=false + diagnostics(CSxxxx)。
    exp7 第一版只读外层 result ⇒ 把 CS0117 当成"返回 None 的活代码"。"""
    r = cmd("script", "eval", "--code", code, timeout=timeout)
    if not r.success:
        return False, "ERR " + str(r.errors[:1])[:180]
    res = (r.data or {}).get("result") or {}
    if not isinstance(res, dict):
        return True, str(res)[:300]
    if not res.get("success", True):
        dg = res.get("diagnostics") or []
        msg = ";".join(x.get("message", "") for x in dg[:2]) if dg else res.get("error", "?")
        return False, f"COMPILE_FAIL {msg[:220]}"
    val = res.get("result")
    if val is None:
        val = res.get("output")
    return True, str(val)[:300]


def one_window(tag: str) -> dict:
    """触发一个采集窗，另存 data.json，返回该窗的 batches 相关序列中位。"""
    DATA.unlink(missing_ok=True)
    CMD.write_text(f"start:{WIN}", encoding="utf-8")
    t0 = time.time()
    while time.time() - t0 < 120:
        if DATA.exists():
            try:
                d = json.loads(DATA.read_text(encoding="utf-8"))
                if d.get("why") in ("window_done", "stop"):
                    break
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.7)
    keep = PROBE_DIR / f"exp7_{tag}.json"
    if DATA.exists():
        shutil.copy(DATA, keep)
        d = json.loads(keep.read_text(encoding="utf-8"))
        ser = d.get("series") or {}
        return {
            "n": d.get("n"), "skipped": d.get("skipped"),
            "DynBatches": round(statistics.median(ser.get("Render/DynBatches", [0])), 2),
            "InstBatches": round(statistics.median(ser.get("Render/InstBatches", [0])), 2),
            "StaticBatches": round(statistics.median(ser.get("Render/StaticBatches", [0])), 2),
            "drawCalls": round(statistics.median(ser.get("Render/DrawCalls", [0])), 2),
        }
    return {"n": 0, "error": "窗口未落盘"}


def poll_batches() -> float:
    vals = []
    for _ in range(5):
        rr = cmd("get_performance", timeout=60)
        rd = ((rr.data or {}).get("result") or {}).get("render") or {}
        if isinstance(rd.get("batches"), (int, float)):
            vals.append(rd["batches"])
        time.sleep(0.4)
    return statistics.median(vals) if vals else float("nan")


def main() -> int:
    st0 = cmd("editor status", timeout=90)
    if not st0.success:
        print("!! 夹具编辑器不可达"); return 1
    if field(st0.model_dump(), "playMode") != "stopped":
        print(f"!! playMode={field(st0.model_dump(),'playMode')} 非 stopped，中止"); return 2

    print("[0] F-GPU-01 + 进 Play + 注入 sampler_v3.1")
    if not cmd("scene", "open", "--path", SCENE, timeout=120).success:
        print("!! 场景打开失败"); return 1
    cmd("editor.playmode", "start", timeout=90)
    for _ in range(12):
        time.sleep(4)
        if field(cmd("editor status", timeout=60).model_dump(), "playMode") == "playing":
            break
    else:
        print("!! 未进 Play"); cmd("editor.playmode", "stop", timeout=60); return 1
    dst = PROJECT / SRC.name
    shutil.copy(SRC, dst)
    try:
        rinj = cmd("script", "eval", "--file", dst.name, timeout=300)
    finally:
        dst.unlink(missing_ok=True)
    if not rinj.success:
        print("!! 注入失败"); cmd("editor.playmode", "stop", timeout=60); return 1

    print("[A] 翻转前窗")
    a = one_window("A_before")
    pb_a = poll_batches()
    print("   注入侧:", a)
    print("   轮询 batches:", pb_a)

    print("[翻转] enableDynamicBatching -> true")
    flip = ev('QualitySettings.enableDynamicBatching = true;'
              'return "dyn=" + QualitySettings.enableDynamicBatching;')
    print("   翻转回执:", flip)

    print("[B] 翻转后窗")
    b = one_window("B_after")
    pb_b = poll_batches()
    print("   注入侧:", b)
    print("   轮询 batches:", pb_b)

    ev('QualitySettings.enableDynamicBatching = false; return "restored";')
    print("[4] 停 Play")
    cmd("editor.playmode", "stop", timeout=60)

    print("\n===== 裁决 =====")
    dyn_move = (b.get("DynBatches", 0) or 0) - (a.get("DynBatches", 0) or 0)
    pol_move = (pb_b if pb_b == pb_b else 0) - (pb_a if pb_a == pb_a else 0)
    print(f"   DynBatches 分项位移 = {dyn_move}  轮询 batches 位移 = {pol_move:.1f}")
    if (dyn_move > 0) == (pol_move > 0) and (dyn_move > 0 or pol_move > 0):
        print("   ⇒ 合批开关下分项与轮询同向出现非零 ⇒ 口径吻合，悬案解。")
    elif dyn_move == 0 and pol_move == 0:
        print("   ⇒ 两侧随合批翻转均无变化（SRP 下 dynamic batching 不入这些口径）"
              "⇒ batches 等价性在本管线结构性无从对表，记 G20 续列，非注入失效。")
    else:
        print("   ⇒ 一侧动一侧不动 ⇒ 口径断裂，如实报（不得拿分项冒充汇总）。")
    print("   留痕: exp7_A_before.json / exp7_B_after.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
