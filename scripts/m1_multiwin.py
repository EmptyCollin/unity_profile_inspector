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
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as EV        # noqa: E402  取消通道（父进程立标志）

# 被取消时用的退出码。为什么不用 15：硬杀（TerminateProcess）的退出码就是 15，
# 两者混用会让"自己收尾"与"被强杀"在留痕上看不出区别——而这两种情况的现场
# 完全不同（前者已退 Play、卸了回调；后者编辑器还停在 Play 态）。
RC_CANCELLED = 3

PROJECT = ROOT / "fixtures" / "upi-fixture"
SCENES_DIR = PROJECT / "Assets/UpiFixtures/Scenes"
PROBE = ROOT / "runs/_probe"
CALDIR = ROOT / "calibration"
FULL_SESSION = False
IDLE_ONLY = False

WARMUP = 8            # 每窗口预热丢弃的轮询次数
WINDOW = 20           # 每窗口轮询次数（多窗口设计下缩短单窗口）
POLL = 1.0            # 目标轮询间隔（秒）

# ── 进度上报（守护/控制台的可观测性入口，非判据件）─────────────────
# 三条纪律：
#   ① pid 写自己（os.getpid()）——写父 PID 会让 cancel 误杀守护本身；
#   ② 原子替换（tmp+rename），读方永远不会看到半截 JSON；
#   ③ 上报失败绝不中断采样——观测坏了可以修，采到一半崩了整轮作废。
PROGRESS_FP = ROOT / "runs" / "daemon_progress.json"


def report(stage: str, fixture: str = "", window: int = 0,
           windows_total: int = 0, note: str = "") -> None:
    """进度上报（观测件）。

    ⚠️ 这里**故意**吞掉 OSError，不是遗漏：进度文件坏了可以修，但如果让写
      进度把采样进程带崩，整轮 2.4 分钟的采集与积分就一起作废——观测件
      永远不能变成被观测路径的故障源（本仓纪律）。
      失败不静默于系统之外：守护侧用**进度文件新鲜度**判定（读不到/过期即
      视为无进度上报，UI 显示"进度不可用"），检测责任在那一侧，不在这侧。
    """
    try:
        tmp = PROGRESS_FP.with_name(PROGRESS_FP.name + ".tmp")
        tmp.write_text(json.dumps({
            "pid": os.getpid(), "stage": stage, "fixture": fixture,
            "window": window, "windows_total": windows_total,
            "note": note[:120], "ts": time.strftime("%H:%M:%S"),
            "alive": True}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, PROGRESS_FP)
    except OSError:
        pass


def report_done() -> None:
    """采样退出时把 alive 置假：守护据此区分"结束了"与"卡住了"，
    而不是拿一份陈旧进度文件继续当成在跑。"""
    try:
        cur = json.loads(PROGRESS_FP.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if cur.get("pid") == os.getpid():
        cur["alive"] = False
        cur["ts"] = time.strftime("%H:%M:%S")
        try:
            tmp = PROGRESS_FP.with_name(PROGRESS_FP.name + ".tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, PROGRESS_FP)
        except OSError:
            pass
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


def sync_editor_code(max_wait: int = 240) -> tuple[bool, str]:
    """采前同步编辑器内存程序集与磁盘代码（干净链路实测撞出的状态泄漏）。

    成因：git reset 类文件恢复不触发资产导入——编辑器内存里仍跑回滚前的
    程序集；下一轮采样测的是"盘上没有、内存里有"的代码（00:40 轮 F-CPU-01
    采到修复态 9401 B/帧 ⇒ 缺陷漏检，而盘上文件明明是缺陷态）。
    Refresh 检测到磁盘变化会触发重编译与域重载；无变化则是无害空操作。

    返回 (ok, why)：失败必须带原因——07:17 轮实测撞出"笼统 False 无法区分
    编辑器忙碌与真不一致"，拒采是对的，但拒得说不清就是新的认知债。
    编译失败或超时 ⇒ ok=False，调用方必须拒采（脏数据比采不到更糟）。
    """
    r = cmd("script eval", "--code",
            'UnityEditor.AssetDatabase.Refresh(); return "r";', timeout=60)
    if not r.get("success"):
        return False, f"Refresh 未送达：{str(r.get('errors'))[:120]}"
    # 导入/域重载期间 editor status 会 busy——按 max_wait 整体轮询，
    # 不给独立的 30s 硬窗（实测误拒过一轮）
    deadline = time.time() + max_wait
    last: dict = {}
    while time.time() < deadline:
        time.sleep(6)
        rs = cmd("script recompile_status", timeout=60)
        last = (rs.get("data") or {}).get("result") or {}
        if last.get("failed"):
            errs = [str(e) for e in (last.get("errors") or [])][:2]
            return False, f"编辑器重编译失败（盘上代码编译不过）：{errs}"
        st = (cmd("editor status").get("data") or {}).get("result") or {}
        settled = (not last.get("compiling") and "failed" in last
                   and st.get("status") == "ready"
                   and not st.get("domainReloadInProgress"))
        if settled:
            return True, ""
    return False, (f"同步超时（{max_wait}s）未达 settled："
                   f"recompile={str(last)[:120]}")


def robust(series: list[float]) -> dict:
    med = statistics.median(series)
    mad = statistics.median([abs(x - med) for x in series]) * 1.4826
    uniq = len(set(series))
    return {"n": len(series), "median": med, "mad": mad,
            "unique": uniq, "degenerate": uniq / max(1, len(series)) < 0.1 or mad == 0.0}


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
    report("open_scene", fixture, note=scene.name)
    cmd("scene open", "--path", f"Assets/UpiFixtures/Scenes/Scene_{fixture}.unity")
    time.sleep(5)
    if not wait_ready():
        return {"error": "编辑器未就绪"}
    print("  进 Play:", (cmd("editor.playmode start").get("data") or {}).get("result", "失败"))
    report("play_enter", fixture)
    time.sleep(6)
    def frames_now() -> int | None:
        """读一次 Time.frameCount；读不到返回 None（不猜 0）。"""
        o = ev("return UnityEngine.Time.frameCount;")
        try:
            return int(str(o).strip().strip('"'))
        except (TypeError, ValueError):
            return None

    def frames_advancing(wait_s: float = 3.0) -> tuple[bool, str]:
        """失焦停帧守卫（DESIGN §12 实测：失焦即停出帧）。

        为什么必须实测而不是只设 runInBackground=true：那个开关只保证
        "后台时出帧"，窗口被最小化/全屏应用抢占等情况下实测照样会停帧。
        停帧时 get_performance 读的是**上一帧快照**（§12 另一条实测），
        于是数值恒定不变、MAD=0，判据链会把一个"根本没在跑"的现场
        读成"极稳定的正常水位"——这是最危险的失效形态：既非报错也非检出，
        而是把停摆冒充成健康。宁可这一轮不采（专用退出码，父进程记盲区）。
        """
        a = frames_now()
        time.sleep(wait_s)
        b = frames_now()
        if a is None or b is None:
            return False, f"帧号读取失败（a={a} b={b}）"
        if b <= a:
            return False, f"{wait_s:.0f}s 内帧号未推进（{a}→{b}），疑失焦/最小化停帧"
        return True, f"帧号 {a}→{b}（+{b - a}）"

    ev('UnityEngine.Application.targetFrameRate = -1; UnityEngine.Application.runInBackground = true; return "uncapped";')

    # 失焦守卫：进 Play 后先确认"真的在出帧"再采。
    ok_f, msg_f = frames_advancing()
    if not ok_f:
        print("  !! 采前体检未过:", msg_f)
        # 必须先退 Play 再返回：不停 Play 就退出，会把编辑器留在播放态，
        # 下一个进程进来读到的是脏现场（这正是要防的事，不能自己制造）
        cmd("editor.playmode stop")
        time.sleep(4)
        report("precondition_failed", fixture, note=msg_f[:100])
        return {"error": f"采前体检未过：{msg_f}"}
    print("  采前体检:", msg_f)

    if FULL_SESSION:
        out = ev(SAMPLER_SRC, timeout=260)
        print("  A2 注入:", out[:90])
        report("a2_inject", fixture, note=out[:80])
        if out.startswith("ERR") or not ("ready" in out or "alive" in out):
            cmd("editor.playmode stop"); time.sleep(4)
            return {"error": "A2 注入失败: " + out[:200]}

    wins = []
    for i in range(windows):
        # ── 取消检查点（窗口边界）──
        # 为什么放这里：一个窗口约 20~30 秒（预热 + 20 次轮询 + A2 录制），
        # 是最细又不打断单次采集的粒度。再往下放（poll 之间）会把一个窗口
        # 采成半截，那个窗口的中位数就不可用了——宁可少一个完整窗口，
        # 也不要一个残缺窗口进统计（I1）。
        if EV.cancelled_for_me():
            print(f"    取消：在第 {i + 1}/{windows} 个窗口边界收尾")
            # 收尾必须做，这正是优雅取消比硬杀值钱的地方：
            # 不退 Play 会把编辑器留在播放态，下一个进程进来就是脏现场
            cmd("editor.playmode stop")
            time.sleep(4)
            return {"fixture": fixture, "scene": scene.name,
                    "windows": wins, "cancelled": True}
        nonce = f"{fixture}-{i}-{int(time.time())}"
        print(f"    窗口 {i + 1}/{windows} …")
        report("window", fixture, window=i + 1, windows_total=windows)
        w = one_window(fixture, nonce)
        if w:
            wins.append(w)
    cmd("editor.playmode stop")
    time.sleep(5)
    if not scene.exists():
        return {"error": "场景文件缺失"}
    return {"fixture": fixture, "scene": scene.name, "windows": wins}


def main() -> int:
    global FULL_SESSION, IDLE_ONLY, CALDIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", choices=["none", "idle", "full"])
    ap.add_argument("--fixtures", nargs="*", default=None)
    ap.add_argument("--windows", type=int, default=5)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--no-summary", action="store_true",
                    help="临时工作束（A4 交替块实验等）不落摘要——摘要文件名按会话固定，"
                         "写到隔离目录会留下多份 *_summary.json 误导 load_multwin 的优先级")
    ap.add_argument("--caldir", default=None,
                    help="multiwin 输出目录。验证窗必须用隔离目录（M3）——"
                         "默认 calibration/ 会覆盖标定真相源，跨 attempt 混比正是 G7 要拦的事")
    args = ap.parse_args()

    if args.caldir:
        CALDIR = Path(args.caldir)
    CALDIR.mkdir(parents=True, exist_ok=True)

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

    # 采前同步闸（00:40 轮实测漏检的根治点）：编辑器内存程序集必须与磁盘代码
    # 一致才能采。回滚/手改文件不触发资产导入，脏内存会让采样测到"盘上不存在"
    # 的代码——这种数据比采不到更有害（漏检伪装成正常）。
    fixtures = args.fixtures or (ALL_FIXTURES if FULL_SESSION else ["F-CPU-01", "F-GPU-01"])
    report("sync_editor", windows_total=len(fixtures),
           note="编辑器内存程序集与磁盘代码收敛中")
    ok_sync, why_sync = sync_editor_code()
    if not ok_sync:
        print(f"!! 采前同步未通过——本轮拒采：{why_sync}")
        report("sync_failed", note=why_sync)
        report_done()
        return 2
    doc = {"session": args.session, "windows_target": args.windows,
           "protocol": {"warmup": WARMUP, "window_polls": WINDOW, "poll_interval_s": POLL},
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "fixtures": {}}
    # 增量补采：同会话/同窗口数/同协议的历史场景保留（补漏采的，不覆盖已采的）
    fp_keep = CALDIR / f"multiwin_{args.session}.json"
    if fp_keep.exists():
        try:
            prev = json.loads(fp_keep.read_text(encoding="utf-8"))
            same = (prev.get("session") == args.session
                    and prev.get("windows_target") == args.windows
                    and prev.get("protocol") == doc["protocol"])
            if same:
                for k, v in prev.get("fixtures", {}).items():
                    if k not in fixtures and "windows" in v:
                        doc["fixtures"][k] = v
                if doc["fixtures"]:
                    print("保留已采场景:", list(doc["fixtures"]))
        except (json.JSONDecodeError, OSError):
            pass
    cancelled = False
    for fx in fixtures:
        # 夹具边界也是取消点：一个夹具要 2~3 分钟，只在窗口边界检查的话，
        # 用户取消后还得跑完当前夹具的全部窗口才开始下一个的收尾判断。
        if EV.cancelled_for_me():
            print(f"取消：在夹具边界收尾，已完成 {len(doc['fixtures'])}/{len(fixtures)}")
            cancelled = True
            break
        print(f"[{fx}]")
        res = sample_scene(fx, args.windows)
        doc["fixtures"][fx] = res
        if res.get("cancelled"):
            cancelled = True
        fp = CALDIR / f"multiwin_{args.session}.json"
        fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        done_n = sum(1 for v in doc["fixtures"].values() if "windows" in v)
        report("fixture_done", fx, window=done_n, windows_total=len(fixtures),
               note=f"已采 {done_n}/{len(fixtures)} 个夹具")
        if cancelled:
            break
        if not args.no_summary:
            (CALDIR / f"multiwin_{args.session}_summary.json").write_text(
                json.dumps(summarize_bundle(doc), ensure_ascii=False, indent=2),
                encoding="utf-8")
    if cancelled:
        # 半途束照样落盘（上面每个夹具都写了一次）但**不写摘要**：
        # 摘要会被判据链当完整束消费。以 RC_CANCELLED 退出，父进程据此
        # 记"本轮被取消"而不是"重采失败"——B7 要求两者留痕分开。
        print(f"本轮已取消（退出码 {RC_CANCELLED}）；已采夹具保留在束文件中")
        report_done()
        return RC_CANCELLED
    print("写入", CALDIR / f"multiwin_{args.session}.json")
    report("write_bundle", note=str(CALDIR / f"multiwin_{args.session}.json"))
    report_done()
    return 0


def summarize_bundle(doc: dict) -> dict:
    """原始束 → 摘要束：A2 逐帧序列（110 万点/23.9MB）折成每窗口 median + n。

    等价性已实测逐字段一致（stats_from_windows 本就是"每窗口取 median 再
    跨窗口统计"，median 是等价输入，判据不丢）。摘要入库、原始束本地留档
    并可由 resample 重生成——入库的是结论与可复现路径，不是重生成物。"""
    import copy
    t = copy.deepcopy(doc)
    for _fx, v in t.get("fixtures", {}).items():
        for w in v.get("windows", []):
            a2 = w.get("a2")
            if isinstance(a2, dict):
                for k in list(a2):
                    if isinstance(a2[k], list) and a2[k]:
                        a2[k + "_median"] = float(statistics.median(a2[k]))
                        a2[k + "_n"] = len(a2[k])
                        del a2[k]
    return t


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
