"""常驻守护（用户要求的形态：后台持续跑、手动开关、进度可观测）。

与巡检的关系：本模块不新写检测逻辑，复用 patrol_once 双通路——
它只补三件 patrol 没有的东西：
  ① 生命周期：pid 文件 + 心跳（status 的 updated_at），UI/CLI 靠它判活死；
  ② 控制通道：文件命令队列，采样**开/关/暂停**、立即巡检、取消当轮、
     运行时改配置（改的是 daemon_conf.json，活过重启且优先于 llm_config）；
  ③ 进度事件流：轮次、夹具×窗口、检出、工单、决定、被忽略的命令，
     全部落 daemon_events.jsonl——"执行到哪"必须是可读出来的事实。

线程纪律：patrol_once 阻塞约 12 分钟（实测），期间主循环不能停（命令要响应、
心跳要刷新）。故轮次跑在工作线程，主线程只做三件事：消费命令、刷心跳、
轮询子进程进度文件并转发成事件。共享状态只读文件，不共享内存（I9）。

取消语义：cancel 杀掉 m1_multiwin 子进程。被杀的那轮不写"静默"结论
（B7：采集失败≠未检出），记 cancelled 事件——诚实标记半轮作废。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from upi.core import events as E  # noqa: E402
from upi.config.project_profile import active_profile  # noqa: E402
from upi.config.runtime_spec import SPEC, spec_for  # noqa: E402

# 进度文件路径**每次现算**（与总线其余路径同纪律）：写成模块常量时，
# 重定向 E.RUNS 的测试与多实例部署都还在读真实仓库的进度文件——
# 表现为"进度永远不动"的假故障，排查方向整个错位。
def _progress_path():
    return E.RUNS / "daemon_progress.json"
# 命令类 span 的落点目录名：引用总线的单一真相源（E.CMD_TRACE_DIR），
# 不在这里另写一份字面量——写入方与读取方各存一份迟早漂移，
# 漂移的后果是命令历史静默不进统计（G13），而这正是最难发现的一类错。
CMD_TRACE_DIR = E.CMD_TRACE_DIR


class _CmdTracer:
    """命令动作的 trace span（I10/G13：无 trace 不入统计）。

    为什么不塞进轮次 trace：命令（取消/开关/改配置/批准）发生在轮次之外，
    混进 patrol_* 目录会让统计层分不清"这轮花了多少"与"用户按了几次"。
    ⚠️ seq 必须从既有文件续号：每次实例化都从 0 起的话，守护重启后的新
      span 会与旧 span 同号，而聚合层按 (run_id, seq) 去重 ⇒ 新留痕被当
      重复丢弃，等于命令历史凭空消失。
    """

    def __init__(self, runs_root: Path):
        self.dir = runs_root / CMD_TRACE_DIR
        self.path = self.dir / "trace.jsonl"
        # run_id 用固定目录名而不是 daemon_<pid>：每次重启换 id 会让
        # "命令历史"在统计里散成一片，看起来像从没发生过（同一目录的留痕
        # 本该是同一条时间线）。seq 续号已保证跨重启不撞号。
        self.run_id = CMD_TRACE_DIR
        self.seq = 0
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.path.open(encoding="utf-8") as f:
                self.seq = sum(1 for ln in f if ln.strip())
        except OSError:
            pass

    def emit(self, stage: str, ms: float = 0, exit_status: int = 0, **fields):
        self.seq += 1
        rec = {"run_id": self.run_id, "stage": stage, "seq": self.seq,
               "wallclock_ms": int(ms), "exit_status": int(exit_status),
               "environment_fingerprint": "daemon-local",
               "emitted_at": E.now(), **fields}
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            # span 写不进不能打断命令处理（否则"记不上账"变成"服务不可用"），
            # 但必须留痕：I10 的"无 trace 不入统计"要靠这条才能被发现
            E.emit("cmd_trace_write_failed", stage=stage, error=str(e)[:120])


def install_signals(owner) -> dict:
    """装信号处理器：**只置停止标志，绝不在这里做不可逆动作**。

    为什么值得装：无处理器时 Windows 对 CTRL_BREAK、POSIX 对 SIGTERM 都是
    默认终止——finally 一概不跑，于是 pid 文件残留、采样子进程变孤儿、
    连停止事件都没有（本轮实测正是这个形态）。

    ⚠️ 能力边界必须如实说，不能"装了信号处理器"就当已经安全：
      · 以 DETACHED_PROCESS 起的守护**没有控制台**，CTRL_C/CTRL_BREAK
        送不到它，这两个处理器在那种启动方式下永不被触发；
      · Windows 上 SIGTERM 根本不可捕获（os.kill 的 SIGTERM 就是硬杀）；
      · 真正会命中的场景是前台 `upi dev monitor` 与 POSIX 部署。
      所以优雅停止的**主路径仍是命令通道（quit）**，信号只是第二道防线。
      返回值如实记录装上了哪些，UI/报告据此判断而不是默认相信。
    """
    import signal as _sig
    names = ["SIGINT", "SIGTERM"]
    if hasattr(_sig, "SIGBREAK"):
        names.append("SIGBREAK")
    done = {}
    for n in names:
        s = getattr(_sig, n, None)
        if s is None:
            done[n] = "absent"
            continue

        def _handler(signum, _frame, _name=n):
            # 只做一件事：请求停止。清理交给主循环 finally（它会收子进程、
            # 清 pid、发停止事件）。在信号处理器里 unlink/kill 都不安全。
            owner.stop_requested = True
            try:
                E.emit("daemon_signal", name=_name, signum=int(signum),
                       note="仅置停止标志，清理走主循环 finally")
            except OSError:
                pass
        try:
            _sig.signal(s, _handler)
            done[n] = "installed"
        except (OSError, ValueError, RuntimeError) as e:
            # 非主线程装载会抛 ValueError：不该因此让守护起不来，但要记下来
            done[n] = f"failed:{type(e).__name__}"
    return done


def _deep_merge(base: dict, over: dict) -> dict:
    """字典递归合并：over 覆盖 base，但**子字典逐层并**而不是整块顶掉。

    规则只有两条，都不含糊：
      · 两边同键都是 dict ⇒ 递归合并；
      · 其余情况（含 list/标量/类型不同）⇒ over 的值整体替换。
    list 选替换而非逐项并：fixtures 清单的语义就是"覆盖层说了算"，
    逐项并不会产生"A 删了某夹具、B 加回来"这类无法解释的中间态。
    """
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_runtime_conf() -> dict:
    """llm_config 的 pipeline.monitor 打底，daemon_conf.json 覆盖。
    UI 改的配置活过重启（运行时开关与代码默认分离）。

    ⚠️ fail-closed（自愈审计第 1 项）：配置读不出来时**默认关采样**，
      不是"用保守默认继续跑"。旧写法 `except: base = {}` 再
      `merged.get("enabled", True)`——含义是"配置文件坏了就自动开始采样"，
      而采样一轮要占编辑器约 25 分钟并花积分。本轮实测真撞过一次：
      冒烟测试时 daemon_conf.json 被清掉，回落到 llm_config 的 enabled=true，
      守护一上岗就排了一轮真采样，14 秒后才被我取消，证据至今留在 runs/。
      坏配置时人需要的是"停下来告诉我"，不是"替我决定继续跑"。
    """
    from upi.config.settings import load_config
    degraded = []
    try:
        base = load_config(ROOT / "llm_config.yaml").monitor().model_dump()
    except Exception as e:  # noqa: BLE001
        base = {}
        degraded.append(f"monitor_conf_unreadable:{type(e).__name__}")
        E.emit("config_read_failed", source="llm_config.yaml",
               error=f"{type(e).__name__}: {str(e)[:160]}",
               action="sampling_disabled_fail_closed")
    ov = E.load_conf()
    # ⚠️ 必须**递归**合并，不能 {**base, **ov}：覆盖层现在会写嵌套子键
    #   （episode.heartbeat_max_age_s、budget.max_review_rounds、auto_fix.*）。
    #   浅合并时 ov["episode"] = {"heartbeat_max_age_s": 20} 会整个顶掉
    #   base["episode"]，把探针路径 jsonl/heartbeat 从配置里抹掉——
    #   patrol_episode 于是报"探针归档不存在（先跑 m2a_probe 布置）"，
    #   发作通路静默退化成盲区，而且留痕把成因指向"没布置探针"，
    #   引着人去重布一套本来好好的探针。旧代码只对 auto_fix 做深合并
    #   就是这个坑的补丁，但补一个漏两个；改递归合并后一类问题一起消失。
    #   列表按整体替换（fixtures 清单本就该被覆盖层整条改掉，不是逐项并）。
    merged = _deep_merge(base, ov)
    # 两处都没明确写 enabled 才算"读不出来"：缺键 ⇒ False（fail-closed）
    has_explicit = ("enabled" in base) or ("enabled" in ov)
    if not has_explicit:
        degraded.append("enabled_absent_in_both_sources")
        E.emit("config_incomplete", reason="enabled 未在任何配置源声明",
               action="sampling_disabled_fail_closed")
    merged["enabled"] = bool(merged.get("enabled", False))
    # 阶段 7：缺省清单来自 profile（对照 + 被测机制）。
    # 优先级是"配置文件显式声明 > 档案缺省"（用户写的意图优先，与 enabled
    # 同规则）。⚠️ 判"有没有显式声明"不能用 `"fixtures" in merged`：
    #   MonitorConfig.fixtures 有 default_factory=list，model_dump() **永远**
    #   会带一个 fixtures 键（没写就是 []）——那样档案缺省永远不生效，
    #   而且"用户没写"会被当成"清单与档案不一致"告警，把没配错的东西
    #   报成配错了。判据只能是"值非空"。
    _pf = active_profile()
    _prof_fixtures = _pf.fixture_universe()
    _cfg_fixtures = [f for f in (merged.get("fixtures") or []) if f]
    if not _cfg_fixtures:
        merged["fixtures"] = _prof_fixtures
    else:
        merged["fixtures"] = _cfg_fixtures
        # 换工程时 yaml 里遗留的旧清单会让守护去采新工程里根本不存在的
        # 机制——表现为每轮大面积采集失败或静默少采，而排查的人第一反应
        # 是"档案配错了"，方向整个错位。所以不一致必须当场说出来。
        _extra = [f for f in _cfg_fixtures if f not in _prof_fixtures]
        _absent = [f for f in _prof_fixtures if f not in _cfg_fixtures]
        if _extra or _absent:
            degraded.append(f"fixtures_mismatch(in={_extra},out={_absent})")
            E.emit("profile_fixtures_mismatch", profile=_pf.profile_id,
                   configured=_cfg_fixtures, from_profile=_prof_fixtures,
                   extra=_extra, not_in_profile=_absent,
                   note="配置文件显式声明优先于档案；若这是换工程留下的旧清单，"
                        "请删掉 pipeline.monitor.fixtures 让它回落到档案")
    if degraded:
        merged.setdefault("degraded", []).extend(degraded)
    return merged


class Daemon:
    def __init__(self):
        self.conf = load_runtime_conf()
        self.busy = False
        self.paused = False
        self.stop_requested = False
        self.child_pid: int | None = None
        self._lock = threading.Lock()
        self._round_err: str | None = None
        self.consecutive_failures = 0
        self.last_progress_sig = None
        # 命令 span 与信号装载状态。用 __new__ 构造实例的测试走不到这里，
        # 所以下面用 _span_ctx() 惰性取，不直接引用这两个属性。
        self.signals: dict = {}
        self._cmd_tracer = _CmdTracer(E.RUNS)

    def _span_ctx(self):
        """取命令追踪器（惰性）。

        为什么要惰性：离线测试用 Daemon.__new__ 造实例、绕过 __init__，
        直接读 self._cmd_tracer 会 AttributeError——那会让"实现没错、
        测试挂了"的假故障再出现一次（本轮已撞过多起同类）。
        """
        t = getattr(self, "_cmd_tracer", None)
        if t is None:
            t = self._cmd_tracer = _CmdTracer(E.RUNS)
        return t

    # ───────────────────────── 轮次执行 ─────────────────────────

    def _progress_poller(self):
        """把子进程写的进度文件转发成事件（每个不同状态只发一次）。"""
        while self.busy and not self.stop_requested:
            try:
                pg = json.loads(_progress_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(2)
                continue
            if pg.get("pid"):
                self.child_pid = int(pg["pid"])
            sig = (pg.get("fixture"), pg.get("stage"), pg.get("window"))
            if sig != self.last_progress_sig and pg.get("alive"):
                self.last_progress_sig = sig
                E.emit("progress", fixture=pg.get("fixture"),
                       stage=pg.get("stage"), window=pg.get("window"),
                       windows_total=pg.get("windows_total"),
                       note=str(pg.get("note", ""))[:80])
            time.sleep(2.5)

    def _round(self):
        from upi.agents.patrol import patrol_once
        t0 = time.time()
        self._round_err = None
        try:
            rec = patrol_once(dict(self.conf))
        except Exception as e:  # noqa: BLE001 — 单轮失败不杀守护
            self._round_err = f"{type(e).__name__}: {str(e)[:200]}"
            rec = None
        dur = round(time.time() - t0, 1)
        with self._lock:
            self.busy = False
        if rec is None:
            self.consecutive_failures += 1
            E.emit("round_error", seconds=dur, error=self._round_err,
                   consecutive_failures=self.consecutive_failures)
            return
        # 采集失败（有盲区、无检出）算半失败：静默轮≠成功轮，
        # 连续静默失败要能被 max_consecutive_failures 抓住（B6 交互版）。
        # ⚠️ 但**取消轮不计**：取消同样带 blindspots（半途束不可判），
        #   若一并累加，用户连按 3 次取消就会把守护判成"仪器故障"自行退出——
        #   人的正常操作不该消耗故障预算，也不该在统计里冒充采集失败。
        was_cancelled = bool(rec.get("cancelled"))
        if was_cancelled:
            pass                       # 既不加分也不清零：本轮不由仪器决定
        else:
            self.consecutive_failures = 0 if not rec.get("blindspots") else \
                self.consecutive_failures + 1
        n = len(rec["episode"]["findings"]) + len(rec["waterline"]["findings"])
        blind = rec.get("blindspots") or []
        wakes = []
        for fx, w in (rec.get("wake") or {}).items():
            wakes.append({"fixture": fx, "woken": w.get("woken"),
                          "ticket": w.get("approval"), "why": w.get("why")})
            if w.get("approval"):
                E.emit("ticket", fixture=fx, run_id=w.get("run_id"),
                       md=w["approval"])
        # cancel_requested（人叫过停）与 cancelled（采集真的在边界停下来了）
        # 是两个事实，必须分开记：只记前者的话，"按了取消但子进程没停下来"
        # 这种最该被发现的情况会被写成"已取消"，等于假安心。
        E.emit("round_finished", seconds=dur, findings=n, quiet=rec["quiet"],
               blindspots=blind, wakes=wakes,
               cancel_requested=bool(self.stop_requested),
               cancelled=was_cancelled,
               cancelled_clean=rec.get("cancelled_clean"))
        E.write_status({
            "last_round": {"ts": rec["ts"], "seconds": dur, "findings": n,
                           "quiet": rec["quiet"], "blindspots": blind,
                           "wakes": wakes, "error": self._round_err,
                           "cancel_requested": bool(self.stop_requested),
                           "cancelled": was_cancelled,
                           "cancelled_clean": rec.get("cancelled_clean")}})

    # ───────────────────────── 命令消费 ─────────────────────────

    def _apply_cmd(self, c: dict):
        """命令执行的**带 span 包装**（I10/G13）。

        为什么单独包一层：命令（开关/取消/改配置/退出）是人机交互的全部
        动作，过去只发事件不出 span ⇒ 统计层算"成本"时完全看不到它们，
        而 G13 要求"任何进统计的数字都能定位到产生它的 span"。
        包一层而不是在每个分支里塞 emit：分支有 8 个，逐个加迟早漏一个，
        而漏掉的那个恰好是最常按的那个。
        未知命令也要出 span（exit_status=1）：否则"UI 发了个不存在的命令"
        这种实现与界面不同步的问题，在统计里表现为完全无痕。
        """
        cmd = str(c.get("cmd") or "")
        known = ("enable", "disable", "pause", "resume", "run_now",
                 "cancel", "set", "quit", "calibrate")
        t0 = time.perf_counter()
        rc = 0 if cmd in known else 1
        try:
            self._dispatch_cmd(c)
        except Exception as e:  # noqa: BLE001 — 命令炸了不能带崩主循环
            rc = 1
            E.emit("command_error", cmd=cmd,
                   error=f"{type(e).__name__}: {str(e)[:160]}")
            self._span_ctx().emit(stage=f"cmd:{cmd or 'unknown'}",
                                 ms=(time.perf_counter() - t0) * 1000,
                                 exit_status=1,
                                 args_keys=sorted((c.get("args") or {}).keys()))
            raise
        self._span_ctx().emit(stage=f"cmd:{cmd or 'unknown'}",
                              ms=round((time.perf_counter() - t0) * 1000),
                              exit_status=rc,
                              args_keys=sorted((c.get("args") or {}).keys()))


    def _stop_child(self, reason: str) -> None:
        """请采样子进程收尾：文件通道优先，硬杀只作最后的止血。

        ⚠️ 为什么必须抽出来共用：cancel 分支早就改走 request_cancel，而 quit
          分支漏改、仍在 os.kill(child_pid,15)——Windows 上那是 TerminateProcess，
          子进程 finally 不执行 ⇒ Play 不退、A2 回调残留、编辑器被一个
          "看起来已经结束"的进程占住。同一意图两处各写一份，就会漏掉一处
          （本轮实测第 6 次栽在"两处一致"上）。合并成一个入口，
          取消语义才只有一种实现，也不会有人改一处忘另一处。
        """
        pid = self.child_pid
        if not pid:
            # child_pid 靠进度文件反查，子进程刚起还没写进度时是 None；
            # 那时巡检登记在总线里的 pid 才是真值——不能因此拒掉停止请求
            alive, reg = E.child_alive()
            if alive:
                pid = reg
        if pid is None:
            if self.busy:
                # 忙但指名不到进程：只置停止标志，等阶段边界自己结束。
                # 必须说清楚，绝不假装"已取消"——那会让 UI 与统计读到
                # 一个实际没发生的干净收尾
                E.emit("cancel_pending", reason="round_busy_no_child",
                       source=reason,
                       note="已置停止标志，将在阶段边界结束；"
                            "采集子进程未登记，无法指名取消")
            return
        if E.request_cancel(pid, reason=reason):
            E.emit("cancel_sent", child_pid=pid, source=reason,
                   mode="graceful_file_channel")
            return
        # 标志写不下去（磁盘满/权限）才硬杀止血，并如实标注现场可能未收尾
        E.emit("cancel_flag_write_failed", child_pid=pid, source=reason,
               note="取消标志落盘失败，改硬杀兜底：子进程 finally 不会执行")
        try:
            os.kill(pid, 15)
            E.emit("child_hard_killed", pid=pid, reason=f"{reason}_fallback")
        except OSError as e:
            E.emit("cancel_kill_failed", child_pid=pid, error=str(e)[:120])

    def _dispatch_cmd(self, c: dict):
        cmd, args = c.get("cmd", ""), c.get("args") or {}
        known = ("enable", "disable", "pause", "resume", "run_now",
                 "cancel", "set", "quit", "calibrate")
        if cmd not in known:
            E.emit("command_ignored", reason="unknown_cmd", cmd=cmd)
            return
        if cmd == "enable":
            self.conf["enabled"] = True
            E.save_conf({**E.load_conf(), "enabled": True})   # 合并存盘：
            E.emit("sampling_enabled")                        # 整文件覆盖会抹掉
        elif cmd == "disable":                                # auto_fix 等其他键
            self.conf["enabled"] = False
            E.save_conf({**E.load_conf(), "enabled": False})
            E.emit("sampling_disabled")
        elif cmd == "pause":
            self.paused = True
            E.emit("paused")
        elif cmd == "resume":
            self.paused = False
            E.emit("resumed")
        elif cmd == "run_now":
            if self.busy:
                E.emit("command_ignored", reason="round_busy", cmd=cmd)
            else:
                self._start_round("manual")
        elif cmd == "calibrate":
            # 标定与巡检抢的是同一个编辑器，必须走同一条忙碌闸；否则"刚按 l
            # 又按 r"会让两个 m1_multiwin 同时进 Play（G7 现场直接污染）。
            # 单开线程而不就地跑：一轮标定十几分钟，同步执行会把命令循环卡死，
            # 期间连"取消"都收不到——那等于把一个可中断操作变成不可中断。
            if self.busy:
                E.emit("command_ignored", reason="round_busy", cmd=cmd)
            else:
                self._start_calibrate()
        elif cmd == "cancel":
            self.stop_requested = True
            self._stop_child("user_cancel")
        elif cmd == "set":
            # 受理口径全部来自 runtime_spec.SPEC，不再在本机写字段白名单：
            # 两处各存一份时，"UI 显示改成功了、守护按未知字段静默丢弃"
            # 这种最难查的假接线就必然会出现（漏一个键没人报错）。
            changed, rejected = {}, []
            for k, v in args.items():
                item = spec_for(k)
                if item is None:
                    rejected.append(f"{k}:unknown")
                    continue
                try:
                    val = item.cast(v)          # 类型 + 上下界，与 UI 同源
                except (ValueError, TypeError) as e:
                    rejected.append(f"{k}:{str(e)[:80]}")
                    continue
                item.set(self.conf, val)        # 内存态立即生效
                changed[k] = val
            if rejected:
                # 越界/未知字段必须留痕并回绝：静默接受等于把"改配置"变成
                # "改不动配置"，而界面上看着像成功了
                E.emit("config_rejected", keys=rejected, cmd="set")
            if changed:
                # ⚠️ 只把**改动项**合进覆盖层，绝不整份 save_conf(self.conf)：
                # self.conf 是"llm_config.yaml 打底 + 覆盖层"的合并结果，整份
                # 写盘会把从没改过的键（fixtures、episode 探针路径等）也固化进
                # daemon_conf.json，此后改 yaml 不再起作用——覆盖层与打底被
                # 一次性焊死，是实测会踩的坑。
                ov = E.load_conf()
                for k in changed:
                    spec_for(k).set(ov, changed[k])
                if not E.save_conf(ov):
                    E.emit("config_save_failed", keys=list(changed),
                           note="本次改动只存在于内存，重启后回落到配置文件值")
                E.emit("config_changed", **changed)
        elif cmd == "quit":
            # 优雅停止同样走统一收尾：漏改这里就等于给"编辑器被孤儿占住"
            # 留了一条活路（历史版本此处直接 os.kill，finally 不执行）
            self._stop_child("daemon_quit")
            self.stop_requested = True
            E.emit("daemon_quit_cmd")

    # ───────────────────────── 主循环 ─────────────────────────

    def _start_round(self, trigger: str):
        with self._lock:
            if self.busy:
                return
            self.busy = True
            self.stop_requested = False
            self.child_pid = None
        self.next_round_at = time.time() + int(self.conf.get("interval_s", 1800))
        E.emit("round_started", trigger=trigger,
               fixtures=self.conf.get("fixtures"),
               sampling=self.conf.get("enabled"),
               auto_fix=self.conf.get("auto_fix"))
        threading.Thread(target=self._round, daemon=True).start()
        threading.Thread(target=self._progress_poller, daemon=True).start()

    def _start_calibrate(self):
        """冷启动标定入口（界面 l）。忙碌簿记与 _start_round 同构——复用同一把
        self.busy 才意味着"标定期间排期轮/手动轮/r 全都进不来"，G7 现场不被抢。
        不起 next_round_at 的更新：标定不是巡检轮，不该顺手把下一轮的排期推后。
        """
        with self._lock:
            if self.busy:
                return
            self.busy = True
            self.stop_requested = False
            self.child_pid = None
        E.emit("calibrate_started", fixtures=self.conf.get("fixtures"),
               windows=int(self.conf.get("windows", 4)))
        threading.Thread(target=self._calibrate, daemon=True).start()
        threading.Thread(target=self._progress_poller, daemon=True).start()

    def _calibrate(self):
        from upi.agents.patrol import calibrate_round
        t0 = time.time()
        err = None
        try:
            res = calibrate_round(dict(self.conf))
        except Exception as e:  # noqa: BLE001 — 标定炸了不杀守护
            err = f"{type(e).__name__}: {str(e)[:200]}"
            res = {"blindspot": err, "thresholds": None}
        dur = round(time.time() - t0, 1)
        with self._lock:
            self.busy = False
        # 事件名与 round_finished 分开：统计层算"轮次成本/故障配额"的分母
        # 只应包含巡检轮，标定是一次性接入动作，混进 rounds 会让"接入一个新工程"
        # 的十几次分钟被算成 N 轮监测（成本轴造假，G13/G15 同源）。
        E.emit("calibrate_finished", seconds=dur,
               blindspot=res.get("blindspot"),
               thresholds=res.get("thresholds"),
               cancelled=bool(res.get("cancelled")),
               cancelled_clean=res.get("cancelled_clean"),
               error=err)

    def _adopt_cmd_channel(self) -> int:
        """接管命令通道，返回"已看到的最大 seq"。

        ⚠️ 重启时必须把遗留的未受理命令显式丢掉并回执：命令通道是单槽，
          留着 pending 会让 UI 此后**永远发不出新命令**（send_cmd_and_wait
          一直拒绝），而守护看起来完全健康——这是引入 ack 机制自己带来的
          死锁，不处理就等于把"读不到结果"升级成"整个交互层不可用"。
          丢弃必须留痕：那条命令可能是用户按下的"取消"或"关采样"。
        同时清扫上一代留下的取消标志（否则新子进程一开窗就被误判要取消）。
        """
        init = E.read_cmd() or {}
        seq = int(init.get("seq", 0) or 0)
        acked = int(init.get("acked_seq", 0) or 0)
        if seq > acked:
            E.emit("command_dropped", seq=seq, cmd=init.get("cmd"),
                   reason="daemon_restart_before_ack")
            E.ack_cmd(seq, dropped="daemon_restart")
        n = E.sweep_cancel_flags()
        res = E.sweep_residuals()
        if n or res["tmp_removed"] or res["pid_removed"]:
            E.emit("startup_sweep", cancel_flags=n, **res)
        return seq

    def run_forever(self) -> int:
        # pid 文件必须真写进去：写失败还继续跑，就会出现"两个守护都在跑但
        # 存活判定只看得到其中一个"（cmd_start 靠 pid 文件防双启）。
        # 这里选择失败即退出，而不是带着不可见的 pid 硬跑。
        try:
            E.atomic_write(E.PID, str(os.getpid()))
        except OSError as e:
            E.emit("daemon_pid_write_failed", error=str(e)[:160],
                   note="pid 写不进去就不启动，防双守护并发抢编辑器")
            return 3
        # 信号处理器：只置停止标志，清理仍走主循环 finally。
        # 结果一并进 daemon_started——DETACHED_PROCESS 起的方式收不到控制台
        # 信号，这条状态是"第二道防线到底有没有"的唯一凭据，不能事后靠猜。
        self.signals = install_signals(self)
        E.emit("daemon_started", pid=os.getpid(),
               interval_s=int(self.conf.get("interval_s", 1800)),
               enabled=bool(self.conf.get("enabled")),
               auto_fix=self.conf.get("auto_fix"),
               signals=self.signals)
        self.next_round_at = time.time()
        self._last_remind_ts = 0.0
        last_seen_seq = self._adopt_cmd_channel()
        try:
            while not self.stop_requested:
                # 决定意向先于命令处理：人刚按下的"批准"排第一优先级
                self._consume_decide()
                c = E.read_cmd()
                if c and int(c.get("seq", 0)) > last_seen_seq:
                    last_seen_seq = int(c["seq"])
                    E.emit("command_received", seq=last_seen_seq,
                           cmd=c.get("cmd"), args=c.get("args") or {})
                    self._apply_cmd(c)
                    # 回执紧跟处理之后：UI 靠它判断"命令真被消费了"，
                    # 而不是"文件写进去了"（两者在守护崩掉的瞬间不等价）
                    E.ack_cmd(last_seen_seq, cmd=c.get("cmd"))
                    E.write_status({"processed_seq": last_seen_seq})

                open_t, go_t = self._tickets()
                if not self.busy and go_t:
                    # 已批准且未续跑：优先接着跑完（人刚做的决定排第一）
                    self._start_resume(*go_t[0])
                elif not self.busy and open_t:
                    # 决定悬而未决：不叠加新轮（避免重复工单），只等并偶尔提醒
                    if time.time() - self._last_remind_ts > 1800:
                        self._last_remind_ts = time.time()
                        E.emit("waiting_on_decision",
                               tickets=[str(p / "request.md") for p in open_t])
                elif (not self.busy and not open_t and not self.paused
                        and time.time() >= self.next_round_at):
                    if self.conf.get("enabled"):
                        self._start_round("interval")
                    else:
                        self.next_round_at = (
                            time.time() + int(self.conf.get("interval_s", 1800)))
                        E.emit("round_skipped", reason="sampling_disabled")

                self._refresh_status()
                if (self.consecutive_failures
                        >= int(self.conf.get("max_consecutive_failures", 3))):
                    E.emit("daemon_exit", reason="consecutive_failures")
                    break
                time.sleep(2)
        except KeyboardInterrupt:
            E.emit("daemon_interrupt")
        finally:
            try:
                E.PID.unlink()
            except OSError as e:
                # 不清掉也不会误判存活（双判据里 pid_alive 会判死），
                # 但残留必须留痕：否则排查"为什么有个死 pid 文件"时毫无线索
                E.emit("pid_cleanup_failed", error=str(e)[:120])
            E.emit("daemon_stopped", pid=os.getpid())
        return 0

    def _tickets(self) -> tuple[list[Path], list[tuple[str, Path]]]:
        """扫工单，分两类：
        open = 无人决定（等，不叠加新轮）
        go   = 守护**该接手续跑**的已批准工单

        go 的判据必须看 run 的事实状态而非只看批准文件（首次启动实测撞出）：
        历史工单里有 CLI 早已跑完的（有 verdict）、也有作废轮被重置的——
        只看"批准过+没标记"会让守护一上岗就把旧修复重新落地一遍。
        真正的待续跑 = 最后一条 fix 停在 awaiting_approval 且整轮还没有
        verdict 且未起过续跑（resume_done 标记先行，防半途崩后重复起）。

        ⚠️ 读不出来的文件**必须留痕**（阶段 2 收敛）：这里曾对 decision.json
          与 state.json 双双 `except: continue`，后果是那张工单既不进 open
          也不进 go——守护永久不续跑、UI 也看不见它，而磁盘上明明有一张
          用户已批准的工单。静默跳过等于把"数据坏了"伪装成"没活儿干"。
        """
        open_t, go = [], []
        # ⚠️ 扫描根必须取 E.RUNS（总线现算路径），不能写死 ROOT/"runs"：
        #   写死会让"重定向总线"的测试与多实例部署都扫到真实仓库，
        #   于是隔离测试读不到自己造的工单（表现为"留痕没产生"的假故障，
        #   实测正是这样误判成实现缺陷的）。
        for d in sorted(E.RUNS.glob("patrol_*/approvals/*")):
            if not (d / "request.json").exists():
                continue
            if _is_open(d):                 # 判据与自检同源（唯一真相源）
                open_t.append(d)
                continue
            dec = d / "decision.json"
            dec_data, dec_err = E.load_json(dec)
            if dec_err:
                # 损坏的决定文件：不能当"未批"静默放过，也不能当"批了"放行
                E.emit("decision_unreadable", ticket=str(d), reason=dec_err)
                continue
            approved = (dec_data or {}).get("decision") == "approve"
            if not approved or (d / "resume_done.json").exists():
                continue
            run_dir = d.parents[1]
            st, st_err = E.load_json(run_dir / "state.json")
            if st_err:
                # state.json 是续跑判据的唯一依据；读不到就如实报警，
                # 让人去修数据，而不是让守护悄悄放弃这张单
                E.emit("ticket_state_unreadable", ticket=str(d),
                       run_id=run_dir.name, reason=st_err)
                continue
            st = st or {}
            fixes = [r for r in st.get("records", []) if r.get("kind") == "fix"]
            last_status = (fixes[-1].get("execute") or {}).get(
                "status") if fixes else None
            if st.get("verdict") is None and last_status == "awaiting_approval":
                go.append((run_dir.name, d))
        return open_t, go

    def _consume_decide(self) -> None:
        """消费决定意向 → 落 decision.json → 提交成功后清理。

        两段式（peek/commit）而不是读后即删：意向先留在盘上，直到
        decision.json 真的写成功才推 committed_seq。守护中途崩了也不丢
        用户的批准，下一轮接着提交（阶段 2 单一写入者链路的可靠性靠这个）。
        """
        data, err = E.peek_decide()
        if err == "missing" or err == "already_committed":
            E.clear_decide()             # 已提交过的垃圾文件顺手清掉
            return
        if err or not data:
            return
        seq = int(data.get("seq", 0) or 0)
        ticket_rel = str(data.get("ticket") or "")
        # 工单路径必须真实存在且属于待批队列——不能凭意向文件里的字符串
        # 就往任意目录写 decision.json（那等于绕过审批校验）
        from upi.core.approval import (ApprovalDecision, ApprovalStore,
                                       read_token)
        run_root = E.RUNS          # 同 _tickets()：只认总线现算的留档根
        cand = [Path(ticket_rel)] if ticket_rel else []
        if cand and not cand[0].is_absolute():
            cand = [run_root / ticket_rel]
        # 意向里的 ticket 有两种合法形态：工单**目录**，或目录下的
        # request.md（状态快照的 open_tickets 与 UI 用的就是正文路径）。
        # 上一版只认目录，于是"UI 真按下批准"必然判 ticket_not_found——
        # 实测这条链路只在 test_daemon 里传目录路径，恰好绕过了，UI 接线
        # 后第一次跑就撞出来。路径形态不该由调用方猜，这里统一归一化。
        cand = [d.parent if d.name == "request.md" else d for d in cand]
        ok_dir = next((d for d in cand
                       if d.exists() and (d / "request.json").exists()), None)
        if ok_dir is None:
            E.emit("decide_rejected", seq=seq, ticket=ticket_rel,
                   reason="ticket_not_found")
            E.commit_decide(seq)         # 认了：不再反复重试一个不存在的工单
            E.clear_decide()
            return
        # 阶段 5 读毕证明：token 由守护**独立复算**（只依赖工单号），不信
        # UI 传来的字符串——两道闸同源但互不依赖，UI 被改坏/绕过也批不了。
        # 对 a/r/d 一律生效：没读完全文的人同样没资格"凭感觉驳回"。
        if str(data.get("token") or "").strip().lower() != \
                read_token(ok_dir.name):
            E.emit("decide_rejected", seq=seq, ticket=ticket_rel,
                   reason="token_mismatch")
            E.commit_decide(seq)         # 坏意向认掉，防死循环重试
            E.clear_decide()
            return
        try:
            dec = ApprovalDecision(
                request_id=ok_dir.name,
                decision=data.get("decision") or "",
                approver=data.get("approver") or "",
                note=data.get("note") or "")
        except Exception as e:  # noqa: BLE001 — 意向不合法=没有有效决定，绝不放行
            E.emit("decide_rejected", seq=seq, ticket=ticket_rel,
                   reason=f"invalid_decision:{str(e)[:120]}")
            E.commit_decide(seq)         # 同样认掉，避免死循环重试坏意向
            E.clear_decide()
            return
        store = ApprovalStore(ok_dir.parents[1])
        if not store.write_decision(ok_dir.name, dec, source="ui"):
            # 落盘失败：不 commit，下一轮自动重试（意向仍在盘上）
            E.emit("decide_write_failed", seq=seq, ticket=ticket_rel)
            return
        E.commit_decide(seq)
        E.clear_decide()

    def _start_resume(self, run_id: str, ticket: Path):
        """续跑异步化：_resume_ticket 阻塞约 7 分钟（一次干净 attempt 实测：
        before+after 各 2.4min 重采 + 编译 + 判定），放主循环里跑会让心跳停更、
        命令通道失去响应——UI 会把活守护看成死了。"""
        with self._lock:
            if self.busy:
                return
            self.busy = True
            self.stop_requested = False
        threading.Thread(target=self._resume_with_lease,
                         args=(run_id, ticket), daemon=True).start()

    def _resume_with_lease(self, run_id: str, ticket: Path) -> None:
        """抢租约 → 续跑 → 释放。抢不到说明别人（另一代守护/开发通道）在跑。

        为什么必须限次：没有 TTL 的"标记文件"会在崩于 unlink 之前时永久卡住
        这张工单（实测缺口）；但 TTL 若短于真实续跑耗时（约 7~31 分钟），
        正常运行的任务会被别人当"崩了"抢走，同一工单跑两遍——那比卡住更糟。
        判据用 events.LEASE_TTL_S（45 分钟）：既覆盖最长干净 attempt，
        又保证崩溃后最终能自愈。
        """
        if not E.acquire_lease(ticket, holder=os.getpid()):
            E.emit("resume_skipped", run_id=run_id, reason="lease_held",
                   lease=E.lease_state(ticket))
            return
        try:
            self._run_resume_ticket(run_id, ticket)
        finally:
            E.release_lease(ticket)

    def _run_resume_ticket(self, run_id: str, ticket: Path):
        """工单批准后由守护续跑：等价 python -m upi dev resume --run <id>。
        正式审批路径是界面按 d（逐页读完 + 页脚码），这里是守护自己接力。"""
        E.write_json(ticket / "resume_done.json",
                     {"started_at": E.now(), "pid": os.getpid()},
                     what="resume_done", strict=False)
        E.emit("resume_started", run_id=run_id,
               ticket=str(ticket / "request.md"))
        from upi.agents.nodes import make_nodes
        from upi.agents.orchestrator import Runner
        # 续跑前先确认状态文件真能读：读不到就是"不知道要续什么"，
        # 绝不能用空态硬跑一遍修复（那会把未知当成"没做过"）
        st, err = E.load_json(E.RUNS / run_id / "state.json")
        if err or not st:
            E.emit("resume_failed", run_id=run_id,
                   error=f"state_unreadable: {err or 'empty'}")
            with self._lock:
                self.busy = False
            return
        runner = Runner(run_id, st["fixture"])
        nodes = make_nodes(runner.dir, live_llm=bool(
            (self.conf.get("auto_fix") or {}).get("live_llm")),
            approval_note="")
        try:
            runner.resume(nodes, entry="fix")
            st2, err2 = E.load_json(runner.dir / "state.json")
            if err2:
                # 跑完了却读不到判定 ⇒ 不能报"成功"，也不能报"失败"，
                # 只能如实标 unverifiable 并保留工单现场
                E.emit("resume_unverifiable", run_id=run_id, reason=err2)
                with self._lock:
                    self.busy = False
                return
            v = (st2 or {}).get("verdict") or {}
            E.emit("resume_finished", run_id=run_id,
                   status=v.get("status"), route=(st2 or {}).get("route"),
                   error=str((st2 or {}).get("error"))[:180])
            with self._lock:
                self.busy = False
        except Exception as e:  # noqa: BLE001
            ticket.joinpath("resume_done.json").unlink(missing_ok=True)
            E.emit("resume_failed", run_id=run_id,
                   error=f"{type(e).__name__}: {str(e)[:200]}")
            with self._lock:
                self.busy = False

    def _refresh_status(self):
        n_rounds = n_find = n_tk = 0
        # 告警汇总：degraded/write_failed 这类"本轮没坏但有些事没做成"
        # 必须有读者。写进 status 后 UI 直接显示——否则生产者存在而消费者
        # 为零，告警依旧进黑洞，只是换了个地方。
        alerts: list[dict] = []
        for ev in reversed(E.tail_events(400)):
            k = ev.get("kind")
            if k == "round_finished":
                n_rounds += 1
                n_find += int(ev.get("findings") or 0)
            elif k == "ticket":
                n_tk += 1
            elif k in ("state_write_failed", "write_failed", "conf_unreadable",
                       "decision_unreadable", "ticket_state_unreadable",
                       "cmd_seq_unreadable", "decide_write_failed",
                       "orphan_child_holding_cancel", "command_dropped"):
                if len(alerts) < 6:
                    alerts.append({
                        "kind": k, "ts": ev.get("ts"),
                        "what": ev.get("what") or ev.get("stage")
                                or ev.get("reason") or "",
                        "detail": str(ev.get("error") or ev.get("reason")
                                      or ev.get("path") or "")[:90]})
        E.write_status({
            "pid": os.getpid(),
            "sampling_enabled": bool(self.conf.get("enabled")),
            "paused": self.paused,
            "busy": self.busy,
            "phase": ("round_running" if self.busy else
                      ("paused" if self.paused else "idle_waiting")),
            "next_round_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S",
                time.localtime(getattr(self, "next_round_at", time.time()))),
            "counters": {"rounds": n_rounds, "findings": n_find,
                         "tickets": n_tk},
            "alerts": alerts,
            "degraded": bool(alerts),
            "open_tickets": [str(p / "request.md")
                           for p in self._tickets()[0]],
            # 配置快照按 SPEC 逐项导出，而不是手写几个键：UI 表单要显示
            # 每一项的"当前值"，这里漏一个键，界面就会显示"未设"——用户以为
            # 从没配过，其实守护在用默认值跑（又一类"看起来没接上其实接了
            # 半截"）。SPEC 加字段，这里自动跟着走，不需要第二次改。
            "conf": {i.key: i.get(self.conf) for i in SPEC},
            "fixtures": self.conf.get("fixtures")})


def _is_open(d: Path) -> bool:
    """待批判据的唯一真相源：有 request.json 且**无** decision.json。

    ⚠️ 全仓库只此一份。scan_open_tickets()（只读自检用）与 Daemon._tickets()
      （调度用）都必须调它——两处各写一遍 glob+存在性判断，规则就会漂移，
      漂移的表现是"自检说有单、守护说没单"，排查的人只能对着两份代码干瞪眼。
    """
    return (d / "request.json").exists() and not (d / "decision.json").exists()


def scan_open_tickets() -> list[Path]:
    """扫全盘待批工单目录（用 _is_open 判据，与守护调度侧同源）。

    为什么不读 status 快照的 open_tickets：快照只在守护活着时刷新，守护停着
    时那是旧值甚至没值——而那恰好是最需要知道"有没有单子在等"的时刻
    （docs/使用说明.md 曾把这记成已知缺口，阶段 6 补上）。
    """
    return sorted(d for d in E.RUNS.glob("patrol_*/approvals/*") if _is_open(d))


def spawn_detached(wait_s: int = 12) -> tuple[bool, str]:
    """后台分离拉起守护，看到心跳才算成功。返回 (是否成功, 一行人可读结果)。

    为什么放这里而不是留在 __main__ / ui 各写一份：两处曾经**逐字重复**这段
    逻辑（DETACHED_PROCESS 标志、12 次心跳轮询、日志重定向），于是拉起命令
    的字面量也写了两遍。阶段 6 把 monitor 移入 `upi dev` 时，只改一处就会
    让另一处"启动失败"——看起来像守护坏了，其实是分层没同步（分层重构最
    常见的自伤）。收敛成一个函数后，命令名只出现在这里一次。

    ⚠️ 必须真的等到心跳才报成功：Popen 返回只代表进程被创建，不代表守护
      初始化没崩（llm_config 坏、端口占用、包缺失都会让它立刻退出）。
      把"还在起"报成"启动成功"，用户会以为已经在巡检了。
    """
    import subprocess
    running, st = E.daemon_running()
    if running:
        return False, f"守护已在运行（pid={st.get('pid')}），无需重复启动"
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
    log_path = E.RUNS / "daemon_stdout.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab", buffering=0) as log:
            subprocess.Popen([sys.executable, "-m", "upi", "dev", "monitor"],
                             cwd=str(ROOT), stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as e:
        return False, f"启动失败：{type(e).__name__}: {e}"
    for _ in range(wait_s):
        time.sleep(1)
        running, st = E.daemon_running()
        if running:
            return True, f"守护已启动 pid={st.get('pid')}"
    return False, (f"!! {wait_s} 秒内未见守护心跳，启动可能失败。"
                   f"查 {log_path.name} 与 daemon_events.jsonl")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "stop":
        running, st = E.daemon_running()
        if not running:
            print("守护未在运行（无 pid 或心跳过期）")
            return 0
        pid = int(st.get("pid") or 0)
        # 优雅停止优先走命令通道：Windows 上 os.kill(pid,15) 是 TerminateProcess
        # 硬杀，守护不会执行 finally —— pid 文件残留、采样子进程变孤儿继续占
        # 编辑器、也没有停止事件留痕。发 quit → 守护自己收子进程+清 pid+记事件。
        E.send_cmd("quit")
        for _ in range(20):
            time.sleep(1)
            if not E.daemon_running()[0]:
                print(f"守护已优雅停止 pid={pid}")
                return 0
        # 超时兜底：quit 没被消费（主循环卡住），硬杀止血，再清残留 pid
        try:
            os.kill(pid, 15)
            print(f"!! 20s 内 quit 未被消费，已硬杀 pid={pid}"
                  f"（残留 pid 文件需手动清或下次 start 覆盖）")
        except OSError as e:
            print(f"硬杀也失败：{e}")
            return 1
        return 0
    running, _ = E.daemon_running()
    if running:
        print("!! 已有守护在跑（pid 文件 + 心跳双判据）。先 stop 或直接用 UI 控制")
        return 1
    return Daemon().run_forever()


if __name__ == "__main__":
    sys.exit(main())
