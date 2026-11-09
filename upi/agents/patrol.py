"""自主巡检循环（M5' 前置件：让管线作为独立进程盯性能，与 AI 助手无关）。

两条检测通路并行消费，对应 DESIGN §2.4 的时机表：
  发作通路（T1/T2w）：只读常开探针落盘的越界样本 jsonl——本身零采样成本，
    判定用 frame_budget 绝对线（与 m2a_detect_breach 同源检测器）；
  水通路（T3 复用）：每轮按标定协议重采一次束（CLEAN 同轮在场才可比，
    G7），与阈值文件对比——与 m1_detect 同判据链。

关键行为约定（都是实测/文档教训的固化）：
  B1 未检出也留痕：每轮写 patrol_runs.jsonl 一行（含静默轮）——否则"监控
     在跑"和"监控在睡觉"从产物上分不清（I10 无 trace 不入统计的巡检版）；
  B2 探针心跳超龄 ⇒ 发作通路本轮标盲区，不静默（G19/I1：不得把"没读到
     越界"说成"没有越界"）；
  B3 检出 ≠ 修复：auto_fix 只覆盖有 playbook 模板的夹具（现仅 F-CPU-01），
     其余检出后写 findings 队列转人工——§1.7 边界，不是缺陷；
  B4 blocked/semi 档唤起状态机时不预置批准文本：fix 节点自动出五件套工单
     停在 awaiting_approval。巡检进程没有对话者，批准走 decision.json 文件，
     由任何在终端前的人决定（I3：权限不来自执行环境的身份）；
  B5 同夹具冷却期（cooldown_s）内不重复唤起：检出→工单→没人批→下轮又检出
     →又一张单，是无意义的成本与噪声；
  B6 预算护栏随每轮状态机走（G11），连续失败 ≥ max_consecutive_failures
     退出非零——巡检挂了要被知道，不静默转圈。
  B7 采集失败 ≠ 未检出：水通路有"采样有效前提闸"（请求采样的检测器夹具
     判定指标必须在统计里，缺一整轮按盲区记）；盲区轮不产"静默"结论。
     失焦轮实测撞出：五个夹具全部采集失败，旧版却记成"零检出无盲区"。

调度形态：`python -m upi monitor` 前台循环（日志到 stdout + patrol_runs.jsonl），
或 `python -m upi patrol-once` 单轮（外部调度器拉起：Windows 任务计划/CI）。
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from upi.config.settings import load_config                      # noqa: E402
from upi.core import events as E                                  # noqa: E402
from upi.core.breach_detector import findings_from_samples, load_breach_samples  # noqa: E402
from upi.core.detector import DEFAULT_REGISTRY                    # noqa: E402

RUNS = ROOT / "runs"
PATROL_LOG = RUNS / "patrol_runs.jsonl"
STATE_FP = RUNS / "patrol_state.json"

# 请求取消/超时后，给采样子进程自己收尾的宽限（退 Play + 卸回调 + 落盘）。
# 为什么要 150 秒而不是 5 秒：一个完整窗口约 20~30 秒，取消只在窗口边界生效，
# 宽限短于一个窗口就会在"它正打算收尾"时把它硬杀（finally 不执行，编辑器留 Play 态）。
# 模块级而非函数内：离线测试要能把它压到 0，验证兜底 kill 分支真会触发。
GRACE_S = 150


class _ResampleResult:
    """重采子进程的结果包。

    之所以不用 subprocess.CompletedProcess：它的字段名是 returncode，而我这里
    多带两个布尔（cancelled/timed_out），而这两个恰恰决定"这轮该判失败、
    判取消、还是判超时"——三种留痕混成一种就会把半途停止的残缺束
    送进判据链。用 NamedTuple 而不是类，是为了失败留痕的取数写法
    （p.returncode / p.stdout_tail）与 stdlib 保持一致。
    """

    __slots__ = ("rc", "stdout", "stderr", "cancelled", "timed_out")

    def __init__(self, rc, stdout, stderr="", cancelled=False, timed_out=False):
        self.rc = rc
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.cancelled = cancelled
        self.timed_out = timed_out

    @property
    def returncode(self):
        return self.rc

    @property
    def stdout_tail(self) -> str:
        # 截尾要够长：崩溃 traceback 常是最后十几行，截 200 字符只够看个结尾
        return (self.stdout or self.stderr)[-900:]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ────────────────────────── 发作通路（T1/T2w，零采样） ──────────────────────────

def patrol_episode(cfg_mon: dict) -> dict:
    """读探针越界样本并判 finding。心跳超龄⇒盲区声明（B2），不冒充"无发作"。"""
    ep = cfg_mon.get("episode") or {}
    jsonl, hb = ep.get("jsonl"), ep.get("heartbeat")
    out = {"channel": "episode", "findings": [], "blindspot": None}
    if not jsonl or not (ROOT / jsonl).exists():
        out["blindspot"] = "探针归档不存在——发作通路未装（先跑 m2a_probe 布置）"
        return out
    max_age = float(ep.get("heartbeat_max_age_s", 12))
    hb_p = ROOT / hb if hb else None
    if hb_p is None or not hb_p.exists():
        out["blindspot"] = "无探针心跳文件——发作通路状态未知，按盲区处理"
        return out
    age = time.time() - hb_p.stat().st_mtime
    if age > max_age:
        # 注意与 m2a 实测的口径差异：探针只在 Play Mode 采样（回调按帧驱动），
        # 编辑器回编辑态后心跳停更属**正常静默**，不等于探针死了。巡检不能
        # 因此虚报盲区，也不能反过来把"编辑态心跳停"当"确认无发作"——
        # 如实写成 unknown 状态，发作通路只在播放期有检出能力（G19 同源事实）。
        # 盲区轮**不消费偏移**（测试撞出的 bug）：此轮未判定的样本留给下一轮，
        # 否则"本轮没看 ⇒ 样本被标记已处理"= 静默丢失，违反 B2 不冒充无发作。
        out["blindspot"] = (f"心跳超龄（{age:.0f}s>{max_age:.0f}s）：非播放态探针"
                            "本就不更新心跳，发作通路无法证明『静默期无越界』；"
                            "本轮发作判定按盲区记录（G19/G20），样本留待下轮")
        return out
    samples, stats = load_breach_samples(ROOT / jsonl)
    out["n_samples"], out["rejects"] = len(samples), stats.get("reject_reasons", {})
    # 只取本轮新增（按文件偏移增量消费，避免同一条越界反复唤起）。
    # 三态要分清（离线测试撞出的 bug）：seen=0 首轮全量；
    # size≤seen 无新增 ⇒ 样本必须清空——上一版此时保留全量导致重复报；
    # size<seen（轮转/截断，append-only 下不该发生）⇒ 回退全量重读并告警。
    st = _load_state()
    seen_off = st.get("episode_offset", 0)
    try:
        size = (ROOT / jsonl).stat().st_size
    except OSError as e:
        # exists() 刚通过却 stat() 失败 = 文件在这一瞬消失或不可读。
        # 必须标盲区：out 的 blindspot 若留 None，这轮会被当成
        # "看了，没越界"——正是上面注释禁止的"冒充无发作"（B2/G19 同源）。
        # 与心跳超龄同口径：**不消费偏移**，样本留给下一轮。
        out["blindspot"] = (f"探针归档不可读（{type(e).__name__}: {str(e)[:80]}）"
                            "，本轮发作判定按盲区记录，样本留待下轮")
        return out
    if seen_off and size <= seen_off:
        samples = []
        out["n_new_samples"] = 0
        if size < seen_off:
            out["warn"] = f"jsonl 变短（{size}<{seen_off}），append-only 约定被破，本轮全量重读"
            samples, stats = load_breach_samples(ROOT / jsonl)
            out["n_new_samples"] = len(samples)
    elif seen_off:
        # 增量段重新解析（jsonl 逐行自洽，切片安全：写侧是整行 append）
        with (ROOT / jsonl).open(encoding="utf-8") as f:
            f.seek(seen_off)
            tail = f.read()
        tmp = ROOT / "runs" / "_patrol_tail.jsonl"
        tmp.write_text(tail, encoding="utf-8")
        samples, _ = load_breach_samples(tmp)
        tmp.unlink(missing_ok=True)
        out["n_new_samples"] = len(samples)
    else:
        out["n_new_samples"] = len(samples)   # 首轮：全量即新增
    by_fx = {}
    for d in DEFAULT_REGISTRY:
        if d.ptype == "episode":
            by_fx.setdefault(d.fixture, []).append(d)
    judged_any = []
    for fx, dets in by_fx.items():
        for det in dets:
            judged, shape = findings_from_samples(samples, det, "patrol", str(jsonl))
            for f, s in judged:
                judged_any.append({
                    "kind": "issue", "ptype": "episode", "fixture": fx,
                    "detector": det.id, "metric": det.metric,
                    "frame_idx": s.frame_idx,
                    "deviation_ratio": f.issue.deviation_ratio if f.issue else None,
                    "replay_seed": s.replay_seed})
    out["findings"] = judged_any
    out["shape"] = shape if by_fx else {}
    _save_state({"episode_offset": (ROOT / jsonl).stat().st_size})
    return out


# ────────────────────────── 水通路（T3 复用，重采一轮） ──────────────────────────

def spawn_resample(cmd: list[str], log_file):
    """起采样子进程。单独成一个函数只有一个理由：**可注入**。

    离线测试要断言水通路的判据链（CLEAN 前置、静默假象闸、零基线清洗），
    但真起 m1_multiwin 要占编辑器 20+ 分钟。原写法是 mock `subprocess.run`，
    改成 Popen 之后那批桩全部失效（表现为 5 条断言变红，看着像判据链坏了，
    实际是桩挂错了对象）。抽一个函数名出来，测试替身就有明确的挂载点，
    也不会因为实现换了调用形式而静默失效。
    """
    return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                            cwd=str(ROOT))

def patrol_waterline(cfg_mon: dict, workdir: Path) -> dict:
    """按标定协议重采一轮束并跑 m1_detect 判据链。CLEAN 必须同轮在场。"""
    out = {"channel": "waterline", "findings": [], "blindspot": None}
    fixtures = cfg_mon.get("fixtures") or []
    if "CLEAN" not in fixtures:
        out["blindspot"] = "巡检夹具不含 CLEAN——无同期对照，禁止判定（§8.1）"
        return out
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / "scripts" / "m1_multiwin.py"),
           "--session", "full", "--windows", str(int(cfg_mon.get("windows", 4))),
           "--caldir", str(workdir), "--no-summary"]
    # 用 Popen 而不是 subprocess.run：后者拿不到子进程 pid（只能等它结束），
    # 而取消通道必须指名道姓地给那个 pid 立标志。
    # ⚠️ 输出必须**落文件**而不是 PIPE：PIPE 的缓冲只有 64KB 量级，父进程不
    #   实时读就会把子进程写阻塞——m1_multiwin 每个窗口都打进度，一轮 full
    #   会话能打出几十 KB，旧 subprocess.run 靠内部并发读才没出事。自己
    #   Popen 时忘了读就是死锁，直接落文件最稳，且失败留痕照样取得到。
    timeout_s = int(cfg_mon.get("waterline_timeout_s", 900))
    log_fp = workdir / "resample.log"
    try:
        log_f = log_fp.open("w", encoding="utf-8", errors="replace")
        proc = spawn_resample(cmd, log_f)
    except OSError as e:
        out["blindspot"] = f"重采进程无法启动：{type(e).__name__}: {str(e)[:160]}"
        return out
    # 立刻登记 pid：守护的 cancel 命令在子进程写下第一份进度之前也必须能停它
    E.publish_child(proc.pid, role="resample", workdir=str(workdir))
    # 采前体检（含失焦守卫）：把结论也登记进 child 文件，UI 与统计能直接读，
    # 不必再去翻子进程日志。拒采时子进程会自己以 RC_PRECONDITION 退出。
    rc = None
    cancelled = False
    timed_out = False
    t0 = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if E.cancel_requested(proc.pid):
            cancelled = True
            break
        if time.time() - t0 > timeout_s:
            timed_out = True
            break
        time.sleep(2)
    log_f.close()
    stdout_txt = ""
    try:
        stdout_txt = log_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    def _terminate(reason: str) -> None:
        """先请它自己收尾，宽限期内不动手；超时才 kill（G10 只按这个 pid）。"""
        E.request_cancel(proc.pid, reason=reason)
        grace = time.time() + GRACE_S
        while time.time() < grace and proc.poll() is None:
            time.sleep(2)
        if proc.poll() is None:
            proc.kill()                 # 兜底：已给过自行收尾的机会
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            E.emit("child_hard_killed", pid=proc.pid, reason=reason,
                   note="窗口边界收尾未在宽限期内完成，硬杀会丢 finally 清理")
        else:
            E.emit("child_graceful_exit", pid=proc.pid, reason=reason,
                   rc=proc.returncode)

    # 只要进程还活着，就必须先请它收尾——这一步**不能省**：
    # 情况 (a)（父进程先看到取消标志）break 时子进程仍在跑，直接往下走
    # 就等于丢下一个孤儿继续占编辑器；超时同理。
    if proc.poll() is None:
        _terminate("user_cancel" if cancelled else "waterline_timeout")

    rc = proc.returncode
    E.clear_child()
    # 取消有两种到达方式，都要认，但**只能认这两种**：
    #   (a) 父进程在轮询里看到取消标志（子进程还活着）→ 上面已置 cancelled
    #   (b) 子进程自己在窗口边界收尾并以 RC_CANCELLED 退出 → 靠退出码认
    # 只认 (a) 的后果很具体：半途束文件已存在，于是**残缺束被送去判据链**，
    # 得出"零检出"——正是 B7 禁止的"没采到冒充没有异常"。
    # ⚠️ 不能写成 `or E.cancel_requested(pid)`：_terminate() 自己会立标志
    #   （超时收尾也走它），那样每个超时轮都会被后置判定误认成"用户取消"。
    # ⚠️ 补判必须让位于超时：超时轮里 _terminate 也会请求取消，子进程若是
    #   照办并以 rc=3 收尾，"退出码=取消"这条补判会把**归因**从"仪器太慢"
    #   改写成"人不想要了"——两者的处置动作完全不同，这是本轮实测撞出的缺陷。
    if rc == E.RC_CANCELLED and not timed_out:
        cancelled = True
    E.clear_cancel(proc.pid)          # 谁立的标志都在这里收尾，不留给下一轮
    p = _ResampleResult(rc=rc, stdout=stdout_txt, stderr="",
                        cancelled=cancelled, timed_out=timed_out)
    bundle = workdir / "multiwin_full.json"
    # ⚠️ 取消与超时**必须走在 bundle 检查之前**：半途停下时可能已经写出一个
    #   窗口数不足的束文件。把它当正常束送去判据链，就是拿残缺数据采集
    #   下"零检出"的结论——B7 明令禁止的"没采到冒充没有异常"。
    # 超时先判（理由见上：它会借用取消通道，退出码不可用于归因）。
    if timed_out:
        out["blindspot"] = (f"重采超时（>{timeout_s}s，已请求自行收尾）"
                            f"，束不完整不判定；日志尾={p.stdout_tail}")
        out["timed_out"] = True
        return out
    if cancelled:
        # rc=3 说明它自己在窗口边界收尾了（退 Play、卸回调都做了）；
        # rc=15 说明宽限期内没停住被硬杀——**现场状态不同**，留痕必须能分开，
        # 否则排查方向就错了：看到 15 需要人工确认编辑器是否还停在 Play 态。
        clean = rc == E.RC_CANCELLED
        tail = ("子进程已在边界自行收尾，现场干净）" if clean
                else "子进程未在宽限期内自行收尾，已被硬杀；"
                     "编辑器现场可能仍处 Play 态，需人工确认）")
        out["blindspot"] = f"本轮水通路被取消（rc={rc}；{tail}"
        out["cancelled"] = True
        out["cancelled_clean"] = clean
        return out
    # 拿到硬杀码却没人请求过取消：进程被外力终止（外部 taskkill、上一代遗留、
    # 崩溃后被清理）。**不能当普通失败记**——普通失败的现场是干净的，
    # 硬杀的现场可能把编辑器留在 Play 态，下一轮进来就是脏数据。
    # 这里不猜原因，只说"现场状态未知、需人工确认"（I2 证据不足不下结论）。
    if rc == E.RC_HARD_KILL:
        out["blindspot"] = (f"重采进程异常终止（rc={rc}，非本方请求取消）："
                            "退出码等同硬杀，编辑器现场状态未知，"
                            f"需人工确认是否仍处 Play 态；日志尾={p.stdout_tail}")
        out["killed"] = True
        return out
    if not bundle.exists():
        # 失败留痕必须带子进程输出：实测 rc=1 崩溃的 traceback 原本全在 stderr，
        # 只截 stdout 会把真凶记成空串（10:27 轮"重采失败（rc=1）："的成因）。
        # 现在输出合并落同一份日志，所以取 stdout_tail 即可，但措辞要如实
        # 写"日志尾"而不是"stderr尾"——文件里已经不是分流的两个流了。
        out["blindspot"] = (f"重采失败（rc={rc}）：日志尾={p.stdout_tail}")
        return out
    # 判据链完全复用 m1_detect（含 G7 协议指纹校验与标定注入），不改一行判据
    from scripts.m1_detect import (apply_calibration, compare,
                                   latest_calibration, stats_from_windows)
    mw = json.loads(bundle.read_text(encoding="utf-8"))
    fixs = {k: stats_from_windows(v) for k, v in mw.get("fixtures", {}).items()
            if "windows" in v}
    cal = latest_calibration()
    if cal is None:
        out["blindspot"] = "无标定文件，禁止未标定检测（§8.1）"
        return out
    wln = [d for d in DEFAULT_REGISTRY if d.thresholds.threshold_source == "noise_calibration"]
    if apply_calibration(wln, cal) != len(wln):
        out["blindspot"] = "标定未覆盖全部水位检测器（§8.1 全量覆盖）"
        return out
    clean = fixs.get("CLEAN")

    # ── 采样有效前提闸（实测失焦轮撞出的失效模式）：束里夹具无窗口/判定
    # 指标缺失 = 采集失败。判据链对空 stats 只会静默跳过——巡检照抄"零检出"
    # 就是把"没采到"冒充"没有异常"（I1/I2 在调度层的同型错误）。本轮被请求
    # 采样的每个水位检测器夹具，其判定指标必须出现在统计里；任一缺失 ⇒
    # 水通路整轮按盲区记。请求集之外的夹具不算失败（本轮没要它，不覆盖而已）。
    requested = set(cfg_mon.get("fixtures") or [])
    raw_fx = mw.get("fixtures", {})
    missing = []
    for det in wln:
        if det.fixture not in requested:
            continue
        if det.metric not in (fixs.get(det.fixture) or {}):
            err = str((raw_fx.get(det.fixture) or {}).get("error", "无窗口数据"))
            missing.append(f"{det.fixture}({det.metric}): {err[:80]}")
    if clean is None:
        missing.append("CLEAN 无有效统计——无对照基线，禁止判定（§8.1）")
    if missing:
        out["blindspot"] = "采样有效前提不成立，水通路本轮未评估：" + "；".join(missing)
        return out

    for det in wln:
        fx = fixs.get(det.fixture)
        if fx is None or clean is None:
            continue
        issue, detail = compare(det, fx, clean)
        if issue:
            # 量级取 issue.magnitude（compare 的 detail 里同值键名是 delta，
            # 上一版写 detail.get("magnitude") 会静默拿 None——契约键名错）
            dr = detail.get("deviation_ratio")
            undef = isinstance(dr, float) and math.isinf(dr)
            # 零基线指标（F-ANIM-01，A2 债）ratio=inf：那是"无定义"不是
            # "无穷偏离"。落 None + 显式标注——Infinity 进 JSONL 是非标准
            # 扩展（跨工具解析失败），且 None 逼消费方去看标注而不是拿伪数
            # 当依据（I1）。检出本身仍成立（绝对增量门限，A2 既定修法）。
            out["findings"].append({
                "kind": "issue", "ptype": "waterline", "fixture": det.fixture,
                "detector": det.id, "metric": det.metric,
                "magnitude": issue.magnitude,
                "deviation_ratio": None if undef else dr,
                "ratio_undefined_zero_baseline": undef})
    out["bundle"] = str(bundle)
    return out


# ────────────────────────── 巡检状态与唤起 ──────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_FP.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(patch: dict):
    st = {**_load_state(), **patch}
    tmp = STATE_FP.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FP)


def _in_cooldown(fixture: str, cooldown_s: float) -> bool:
    if cooldown_s <= 0:
        return False
    last = _load_state().get("last_wake", {}).get(fixture)
    if not last:
        return False
    try:
        dt = time.time() - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return False
    return dt < cooldown_s


def wake_state_machine(fx_item: dict, cfg_mon: dict) -> dict:
    """检出 ⇒ 唤起状态机（detect→diagnose→plan→fix 出工单后停）。

    巡检进程**没有对话者**：批准文本传空——fix 节点的先报后批闸会生成
    五件套工单并停在 awaiting_approval，由人在终端写 decision.json 决定。
    这正是"独立于 AI 助手"的含义：批准是文件通道，不是对话通道。"""
    run_id = "patrol_" + time.strftime("%Y%m%d_%H%M%S")
    from upi.agents.orchestrator import Runner
    from upi.agents.nodes import make_nodes
    af = cfg_mon.get("auto_fix") or {}
    runner = Runner(run_id, fx_item["fixture"],
                    budget=cfg_mon.get("budget") or None)
    nodes = make_nodes(runner.dir, live_llm=bool(af.get("live_llm", False)),
                       approval_note="")
    # detect 节点会重跑 m1_detect（子进程，与 m1_multiwin 同协议同源），
    # 状态机内部自证检出——巡检传入的 finding 只作路由依据，不冒充状态机输入
    runner.run(nodes)   # 终态从 state.json 读回（run 内部已落盘）
    st = json.loads((runner.dir / "state.json").read_text(encoding="utf-8"))
    ar = st.get("approval_request")
    return {"run_id": run_id, "route": st.get("route"),
            "approval": ar["md"] if ar else None,
            "verdict": (st.get("verdict") or {}).get("status")}


def patrol_once(cfg_mon: dict) -> dict:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    work = RUNS / f"patrol_{stamp}"
    ep = patrol_episode(cfg_mon)
    wl = patrol_waterline(cfg_mon, work)
    wake = {}
    af = cfg_mon.get("auto_fix") or {}
    auto_fx = set(af.get("fixtures") or [])
    cooldown = float(cfg_mon.get("cooldown_s", 0))
    for f in wl["findings"] + ep["findings"]:
        fx = f["fixture"]
        if fx not in auto_fx:
            continue                       # 转人工：进 findings 队列（B3）
        if _in_cooldown(fx, cooldown):
            wake[fx] = {"woken": False, "why": "cooldown"}
            continue
        try:
            res = wake_state_machine(f, cfg_mon)
            wake[fx] = {"woken": True, **res}
            _save_state({"last_wake": {**(_load_state().get("last_wake") or {}),
                                       fx: _now()}})
        except Exception as e:  # noqa: BLE001 — 唤起失败也要留痕且不影响下轮
            wake[fx] = {"woken": False, "error": str(e)[:200]}
    blind = [x for x in (ep.get("blindspot"), wl.get("blindspot")) if x]
    rec = {"ts": _now(), "episode": {k: ep.get(k) for k in
                                      ("findings", "blindspot", "n_samples",
                                       "n_new_samples")},
           "waterline": {"findings": wl["findings"], "blindspot": wl["blindspot"],
                         "cancelled": bool(wl.get("cancelled")),
                         "bundle": wl.get("bundle")},
           "wake": wake,
           # B7 静默语义收紧：静默=采到了且无异常。任一通路未评估时整轮记
           # "未评估"而非"静默"——把采集失败写成绿轮是假安心（G20 同源）。
           "blindspots": blind,
           "quiet": not (ep["findings"] or wl["findings"]) and not blind}
    with PATROL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


def monitor_loop() -> int:
    cfg = load_config(ROOT / "llm_config.yaml")
    mon = cfg.monitor()
    if not mon.enabled:
        print("pipeline.monitor.enabled=false——巡检未启用（配置缺段=不启用，不猜默认）")
        return 2
    print(f"巡检启动：间隔 {mon.interval_s}s，夹具 {mon.fixtures}，"
          f"auto_fix={mon.auto_fix.get('fixtures')} live_llm={mon.auto_fix.get('live_llm')}")
    fails = 0
    while True:
        try:
            rec = patrol_once(mon.model_dump())
            fails = 0
            n_f = len(rec["episode"]["findings"]) + len(rec["waterline"]["findings"])
            blind = rec.get("blindspots") or []
            tag = ("静默" if rec["quiet"] else
                   f"检出 {n_f} 条" if n_f else "未评估（盲区轮，不算静默）")
            print(f"[{rec['ts']}] {tag}" + (f"（盲区 {len(blind)} 项）" if blind else ""))
            for b in blind:
                print("   -", b[:150])
        except KeyboardInterrupt:
            print("巡检手动停止")
            return 0
        except Exception as e:  # noqa: BLE001 — 单轮失败不杀循环，超限才退
            fails += 1
            print(f"[{_now()}] 巡检轮失败 {fails}/{mon.max_consecutive_failures}:",
                  str(e)[:200])
            if fails >= mon.max_consecutive_failures:
                print("!! 连续失败达上限，退出非零（巡检挂了要被知道，B6）")
                return 1
        time.sleep(mon.interval_s)
