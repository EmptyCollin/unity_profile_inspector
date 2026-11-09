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

PROGRESS = ROOT / "runs" / "daemon_progress.json"


def load_runtime_conf() -> dict:
    """llm_config 的 pipeline.monitor 打底，daemon_conf.json 覆盖。
    UI 改的配置活过重启（运行时开关与代码默认分离）。"""
    from upi.config.settings import load_config
    try:
        base = load_config(ROOT / "llm_config.yaml").monitor().model_dump()
    except Exception:  # noqa: BLE001 — 配置坏掉时守护仍可跑，用保守默认
        base = {}
    ov = E.load_conf()
    merged = {**base, **ov}
    if base.get("auto_fix") or ov.get("auto_fix"):
        merged["auto_fix"] = {**(base.get("auto_fix") or {}),
                              **(ov.get("auto_fix") or {})}
    merged["enabled"] = bool(merged.get("enabled", True))
    merged.setdefault("fixtures", ["CLEAN", "F-CPU-01", "F-GPU-01",
                                   "F-ANIM-01", "F-LOAD-01"])
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

    # ───────────────────────── 轮次执行 ─────────────────────────

    def _progress_poller(self):
        """把子进程写的进度文件转发成事件（每个不同状态只发一次）。"""
        while self.busy and not self.stop_requested:
            try:
                pg = json.loads(PROGRESS.read_text(encoding="utf-8"))
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
        # 连续静默失败要能被 max_consecutive_failures 抓住（B6 交互版）
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
        E.emit("round_finished", seconds=dur, findings=n, quiet=rec["quiet"],
               blindspots=blind, wakes=wakes, cancelled=self.stop_requested)
        E.write_status({
            "last_round": {"ts": rec["ts"], "seconds": dur, "findings": n,
                           "quiet": rec["quiet"], "blindspots": blind,
                           "wakes": wakes, "error": self._round_err,
                           "cancelled": self.stop_requested}})

    # ───────────────────────── 命令消费 ─────────────────────────

    def _apply_cmd(self, c: dict):
        cmd, args = c.get("cmd", ""), c.get("args") or {}
        known = ("enable", "disable", "pause", "resume", "run_now",
                 "cancel", "set", "quit")
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
        elif cmd == "cancel":
            if not self.busy or not self.child_pid:
                E.emit("command_ignored", reason="no_round_running", cmd=cmd)
            else:
                self.stop_requested = True
                try:
                    os.kill(self.child_pid, 15)
                    E.emit("cancel_sent", child_pid=self.child_pid)
                except OSError as e:
                    E.emit("cancel_failed", error=str(e)[:120])
        elif cmd == "set":
            changed = {}
            for k, v in args.items():
                if k in ("interval_s", "cooldown_s", "windows",
                         "waterline_timeout_s"):
                    self.conf[k] = int(v)
                    changed[k] = int(v)
                elif k == "live_llm":
                    self.conf.setdefault("auto_fix", {})["live_llm"] = bool(v)
                    changed["live_llm"] = bool(v)
                elif k == "auto_fix_enabled":
                    self.conf.setdefault("auto_fix", {})["enabled"] = bool(v)
                    changed["auto_fix_enabled"] = bool(v)
            if changed:
                E.save_conf(self.conf)
                E.emit("config_changed", **changed)
        elif cmd == "quit":
            # 优雅停止也要收拾采样子进程：只置标志会让 m1_multiwin 变孤儿
            # 继续占住编辑器（硬杀 finally 不执行，更不可能清）
            if self.busy and self.child_pid:
                try:
                    os.kill(self.child_pid, 15)
                    E.emit("cancel_sent", reason="daemon_quit",
                           child_pid=self.child_pid)
                except OSError as e:
                    E.emit("cancel_failed", error=str(e)[:120])
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
        if n:
            E.emit("startup_sweep", cancel_flags=n)
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
        E.emit("daemon_started", pid=os.getpid(),
               interval_s=int(self.conf.get("interval_s", 1800)),
               enabled=bool(self.conf.get("enabled")),
               auto_fix=self.conf.get("auto_fix"))
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
        for d in sorted((ROOT / "runs").glob("patrol_*/approvals/*")):
            if not (d / "request.json").exists():
                continue
            dec = d / "decision.json"
            if not dec.exists():
                open_t.append(d)
                continue
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
        from upi.core.approval import ApprovalDecision, ApprovalStore
        run_root = ROOT / "runs"
        cand = [Path(ticket_rel)] if ticket_rel else []
        if cand and not cand[0].is_absolute():
            cand = [run_root / ticket_rel]
        ok_dir = next((d for d in cand
                       if d.exists() and (d / "request.json").exists()), None)
        if ok_dir is None:
            E.emit("decide_rejected", seq=seq, ticket=ticket_rel,
                   reason="ticket_not_found")
            E.commit_decide(seq)         # 认了：不再反复重试一个不存在的工单
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
        """工单批准后由守护续跑：等价 python -m upi resume --run <id>。"""
        E.write_json(ticket / "resume_done.json",
                     {"started_at": E.now(), "pid": os.getpid()},
                     what="resume_done", strict=False)
        E.emit("resume_started", run_id=run_id,
               ticket=str(ticket / "request.md"))
        from upi.agents.nodes import make_nodes
        from upi.agents.orchestrator import Runner
        # 续跑前先确认状态文件真能读：读不到就是"不知道要续什么"，
        # 绝不能用空态硬跑一遍修复（那会把未知当成"没做过"）
        st, err = E.load_json(ROOT / "runs" / run_id / "state.json")
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
            "conf": {"interval_s": int(self.conf.get("interval_s", 1800)),
                     "cooldown_s": int(self.conf.get("cooldown_s", 0)),
                     "auto_fix": self.conf.get("auto_fix"),
                     "fixtures": self.conf.get("fixtures")}})


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
