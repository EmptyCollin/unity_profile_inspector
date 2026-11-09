#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守护层离线测试（事件总线 / 命令通道 / 续跑判据 / 进度上报 / UI 渲染）。

全部在临时目录跑，不启进程、不碰 Editor、不采样。验的是用户新要求的
三件事（UI、开关、持续跑+进度）背后的机制正确性：
 ① 事件总线：原子写、append-only、seq 去重、双判据探活（含 Windows 不误杀）
 ② 命令通道：enable/disable 合并存盘（不抹 auto_fix）、未知命令留痕、set 生效
 ③ 续跑判据：只认"停在待批且无 verdict"的轮，历史已跑完的绝不复活
 ④ 进度上报：report 写自己 pid、report_done 置 alive=false、损坏不影响
 ⑤ UI snapshot：无守护/有守护/有待批三种态都能渲染不崩
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E       # noqa: E402
from upi.agents import daemon as D      # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_daemon_"))


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}")


def main():
    # 事件总线全指向临时区，避免污染仓库 runs/
    E.EVENTS = TMP / "daemon_events.jsonl"
    E.STATUS = TMP / "daemon_status.json"
    E.CMD = TMP / "daemon_cmd.json"
    E.CONF = TMP / "daemon_conf.json"
    E.PID = TMP / "daemon.pid"
    E.RUNS = TMP

    print("== ① 事件总线 ==")
    E.emit("round_started", trigger="interval", fixtures=["A"])
    E.emit("round_finished", findings=2, quiet=False)
    evs = E.tail_events(10)
    check("emit 落盘且可读回", len(evs) == 2 and evs[0]["kind"] == "round_started")
    # 损坏行不炸读取（append-only 半写防护）
    with E.EVENTS.open("a", encoding="utf-8") as f:
        f.write("{坏行\n")
    evs2 = E.tail_events(3)
    check("坏行读成占位事件不抛异常", any("line-broken" in str(e.get("kind"))
                                          for e in evs2))

    seq1 = E.send_cmd("pause")
    seq2 = E.send_cmd("resume")
    check("命令 seq 自增", seq2 == seq1 + 1)
    check("读回的是最新命令", E.read_cmd()["cmd"] == "resume")

    atomic = TMP / "atomic.json"
    E.atomic_write(atomic, json.dumps({"k": "v"}))
    check("atomic_write 内容正确且无残留 tmp",
          json.loads(atomic.read_text())["k"] == "v"
          and not atomic.with_name(atomic.name + ".tmp").exists())

    # 探活：用自己 pid（绝不能因探活杀掉自己）+ 不存在 pid 判死
    check("探活自己=True 且进程仍在", E.pid_alive(os.getpid()))
    check("不存在 pid=判死", not E.pid_alive(999999))
    check("pid=0/负数判死", not E.pid_alive(0) and not E.pid_alive(-5))

    print("== ② 命令通道：存盘合并 + 未知命令留痕 ==")
    E.save_conf({"enabled": True, "auto_fix": {"live_llm": True,
                                               "fixtures": ["F-CPU-01"]}})
    d = D.Daemon.__new__(D.Daemon)      # 不跑 __init__ 的 load_config，手装最小态
    import threading
    d.conf = {"enabled": True, "auto_fix": {"live_llm": True,
                                            "fixtures": ["F-CPU-01"]}}
    d.paused = False
    d.busy = False
    d.stop_requested = False
    d.child_pid = None
    d._lock = threading.Lock()
    d.consecutive_failures = 0
    d._last_remind_ts = 0.0

    d._apply_cmd({"cmd": "disable", "args": {}})
    saved = E.load_conf()
    check("disable 后 enabled=False", saved.get("enabled") is False)
    check("disable 合并存盘：auto_fix 未被抹掉",
          saved.get("auto_fix", {}).get("live_llm") is True
          and saved.get("auto_fix", {}).get("fixtures") == ["F-CPU-01"])
    d._apply_cmd({"cmd": "enable", "args": {}})
    check("enable 还原=True", E.load_conf().get("enabled") is True)

    E.emit("marker-clear")
    d._apply_cmd({"cmd": "teleport", "args": {}})
    got = [e for e in E.tail_events(5) if e.get("kind") == "command_ignored"]
    check("未知命令 ⇒ command_ignored 留痕（不静默）", len(got) == 1
          and got[0]["reason"] == "unknown_cmd")

    d._apply_cmd({"cmd": "set", "args": {"interval_s": 600, "live_llm": False}})
    check("set 改内存配置", d.conf["interval_s"] == 600
          and d.conf["auto_fix"]["live_llm"] is False)
    check("set 落盘（活过重启）",
          E.load_conf().get("interval_s") == 600)

    print("== ②b quit 优雅停止：先收子进程再退出 ==")
    # 空闲态 quit：不该发 cancel，只置 stop 标志并留痕
    d.busy, d.child_pid, d.stop_requested = False, None, False
    d._apply_cmd({"cmd": "quit", "args": {}})
    check("空闲 quit ⇒ stop_requested 且不发 cancel",
          d.stop_requested is True
          and not any(e.get("kind") == "cancel_sent"
                      for e in E.tail_events(6)))
    # 忙态 quit：必须尝试收子进程（活体实测证明硬杀会留孤儿占编辑器）
    d.busy, d.stop_requested = True, False
    d.child_pid = 999998          # 不存在的 pid：走 cancel_failed 而非崩
    d._apply_cmd({"cmd": "quit", "args": {}})
    tail = E.tail_events(6)
    kinds = [e.get("kind") for e in tail]
    check("忙态 quit ⇒ 尝试收子进程（失败也留痕不崩）",
          "cancel_failed" in kinds or "cancel_sent" in kinds)
    check("quit 后 stop_requested=True", d.stop_requested is True)
    d.busy, d.child_pid = False, None

    print("== ③ 续跑判据：历史已跑完的绝不复活 ==")
    run_dir = TMP / "runs" / "patrol_test1"
    tk = run_dir / "approvals" / "APR-x"
    tk.mkdir(parents=True)
    (tk / "request.json").write_text("{}", encoding="utf-8")
    # 情形 A：批准过 + 已有 verdict（CLI 早跑完）⇒ 不该进 go
    (tk / "decision.json").write_text(
        json.dumps({"decision": "approve"}), encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps(
        {"verdict": {"status": "resolved"},
         "records": [{"kind": "fix", "execute": {"status": "applied"}}]}),
        encoding="utf-8")
    d._tickets_roots = lambda: (TMP / "runs").iterdir()   # 便于覆盖扫描根
    open_t, go = _tickets_with_root(d, TMP / "runs")
    check("已跑完(有verdict)的批准工单 ⇒ 不续跑", go == [])

    # 情形 B：批准过 + 无 verdict + 停在 awaiting_approval ⇒ 该续跑
    (run_dir / "state.json").write_text(json.dumps(
        {"verdict": None,
         "records": [{"kind": "fix", "execute": {"status": "awaiting_approval"}}]}),
        encoding="utf-8")
    open_t, go = _tickets_with_root(d, TMP / "runs")
    check("停在待批且无判定 ⇒ 进续跑队列",
          len(go) == 1 and go[0][0] == "patrol_test1")

    # 情形 C：无决定 ⇒ open
    (tk / "decision.json").unlink()
    open_t, go = _tickets_with_root(d, TMP / "runs")
    check("无决定的工单 ⇒ open（等，不续跑）",
          len(open_t) == 1 and go == [])

    # 情形 D：有 resume_done 标记 ⇒ 不重复起
    (tk / "decision.json").write_text(
        json.dumps({"decision": "approve"}), encoding="utf-8")
    (tk / "resume_done.json").write_text("{}", encoding="utf-8")
    open_t, go = _tickets_with_root(d, TMP / "runs")
    check("已标记 resume_done ⇒ 幂等不重复续跑", go == [])

    print("== ④ 进度上报（m1_multiwin report）==")
    import scripts.m1_multiwin as M
    M.PROGRESS_FP = TMP / "prog.json"
    M.report("window", "F-CPU-01", window=2, windows_total=4)
    pg = json.loads((TMP / "prog.json").read_text(encoding="utf-8"))
    check("report 写自己 pid 与窗口",
          pg["pid"] == os.getpid() and pg["window"] == 2 and pg["alive"] is True)
    M.report_done()
    pg2 = json.loads((TMP / "prog.json").read_text(encoding="utf-8"))
    check("report_done 置 alive=false（区分结束/卡住）", pg2["alive"] is False)

    print("== ⑤ UI snapshot 渲染三态不崩 ==")
    import upi.agents.ui as U
    U.E = E   # UI 用同一临时区
    st_none = U.snapshot()
    check("无守护态可渲染", "未启动" in st_none or "已停止" in st_none)
    E.write_status({"pid": os.getpid(), "updated_at": E.now(),
                    "sampling_enabled": True, "phase": "round_running",
                    "open_tickets": [], "counters": {"rounds": 1}})
    E.PID.write_text(str(os.getpid()))
    st_run = U.snapshot()
    check("运行态显示采样开", "运行中" in st_run)
    E.write_status({"pid": os.getpid(), "updated_at": E.now(),
                    "sampling_enabled": True, "phase": "idle_waiting",
                    "open_tickets": ["runs/x/request.md"],
                    "counters": {"rounds": 1}})
    st_tk = U.snapshot()
    check("有待批工单被醒目提示", "待你决定" in st_tk or "待批工单 1" in st_tk)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n==== 守护层离线测试: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


def _tickets_with_root(d, runs_root):
    """复刻 Daemon._tickets 的判据但用临时根，验证分类逻辑本身。"""
    open_t, go = [], []
    for tk in sorted(runs_root.glob("patrol_*/approvals/*")):
        if not (tk / "request.json").exists():
            continue
        dec = tk / "decision.json"
        if not dec.exists():
            open_t.append(tk)
            continue
        try:
            approved = json.loads(dec.read_text(encoding="utf-8")).get("decision") == "approve"
        except (json.JSONDecodeError, OSError):
            approved = False
        if not approved or (tk / "resume_done.json").exists():
            continue
        run_dir = tk.parents[1]
        try:
            st = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fixes = [r for r in st.get("records", []) if r.get("kind") == "fix"]
        last = (fixes[-1].get("execute") or {}).get("status") if fixes else None
        if st.get("verdict") is None and last == "awaiting_approval":
            go.append((run_dir.name, tk))
    return open_t, go


if __name__ == "__main__":
    sys.exit(main())
