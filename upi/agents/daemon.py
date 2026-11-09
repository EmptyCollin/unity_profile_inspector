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

    def run_forever(self) -> int:
        E.PID.write_text(str(os.getpid()), encoding="utf-8")
        E.emit("daemon_started", pid=os.getpid(),
               interval_s=int(self.conf.get("interval_s", 1800)),
               enabled=bool(self.conf.get("enabled")),
               auto_fix=self.conf.get("auto_fix"))
        self.next_round_at = time.time()
        self._last_remind_ts = 0.0
        last_seen_seq = int((E.read_cmd() or {}).get("seq", 0))
        try:
            while not self.stop_requested:
                c = E.read_cmd()
                if c and int(c.get("seq", 0)) > last_seen_seq:
                    last_seen_seq = int(c["seq"])
                    E.emit("command_received", seq=last_seen_seq,
                           cmd=c.get("cmd"), args=c.get("args") or {})
                    self._apply_cmd(c)
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
            except OSError:
                pass
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
        """
        open_t, go = [], []
        for d in sorted((ROOT / "runs").glob("patrol_*/approvals/*")):
            if not (d / "request.json").exists():
                continue
            dec = d / "decision.json"
            if not dec.exists():
                open_t.append(d)
                continue
            try:
                approved = json.loads(dec.read_text(encoding="utf-8")).get(
                    "decision") == "approve"
            except (json.JSONDecodeError, OSError):
                approved = False
            if not approved or (d / "resume_done.json").exists():
                continue
            run_dir = d.parents[1]
            try:
                st = json.loads((run_dir / "state.json")
                                .read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            fixes = [r for r in st.get("records", []) if r.get("kind") == "fix"]
            last_status = (fixes[-1].get("execute") or {}).get(
                "status") if fixes else None
            if st.get("verdict") is None and last_status == "awaiting_approval":
                go.append((run_dir.name, d))
        return open_t, go

    def _start_resume(self, run_id: str, ticket: Path):
        """续跑异步化：_resume_ticket 阻塞约 7 分钟（一次干净 attempt 实测：
        before+after 各 2.4min 重采 + 编译 + 判定），放主循环里跑会让心跳停更、
        命令通道失去响应——UI 会把活守护看成死了。"""
        with self._lock:
            if self.busy:
                return
            self.busy = True
            self.stop_requested = False
        threading.Thread(target=self._resume_ticket,
                         args=(run_id, ticket), daemon=True).start()
    def _resume_ticket(self, run_id: str, ticket: Path):
        """工单批准后由守护续跑：等价 python -m upi resume --run <id>。"""
        (ticket / "resume_done.json").write_text(json.dumps(
            {"started_at": E.now(), "pid": os.getpid()}), encoding="utf-8")
        E.emit("resume_started", run_id=run_id,
               ticket=str(ticket / "request.md"))
        from upi.agents.nodes import make_nodes
        from upi.agents.orchestrator import Runner
        st = json.loads((ROOT / "runs" / run_id / "state.json")
                        .read_text(encoding="utf-8"))
        runner = Runner(run_id, st["fixture"])
        nodes = make_nodes(runner.dir, live_llm=bool(
            (self.conf.get("auto_fix") or {}).get("live_llm")),
            approval_note="")
        try:
            runner.resume(nodes, entry="fix")
            st2 = json.loads((runner.dir / "state.json")
                             .read_text(encoding="utf-8"))
            v = st2.get("verdict") or {}
            E.emit("resume_finished", run_id=run_id,
                   status=v.get("status"), route=st2.get("route"),
                   error=str(st2.get("error"))[:180])
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
        for ev in reversed(E.tail_events(400)):
            k = ev.get("kind")
            if k == "round_finished":
                n_rounds += 1
                n_find += int(ev.get("findings") or 0)
            elif k == "ticket":
                n_tk += 1
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
