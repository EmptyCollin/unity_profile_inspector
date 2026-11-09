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


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else f"  ← {str(detail)[:120]}"))
    print(f"   [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f"  ← {str(detail)[:120]}"))


def _mk_daemon_state(conf):
    """不跑 __init__（它会读 llm_config），手装最小可运行态。"""
    import threading
    d = D.Daemon.__new__(D.Daemon)
    d.conf = dict(conf)
    d.paused = False
    d.busy = False
    d.stop_requested = False
    d.child_pid = None
    d._lock = threading.Lock()
    d.consecutive_failures = 0
    d._last_remind_ts = 0.0
    d.next_round_at = 0.0
    return d


def main():
    # 事件总线全部指向临时区，避免污染仓库 runs/。
    # 用 E.isolate() 而不是逐条赋 EVENTS/STATUS/CMD/CONF/PID：
    # 总线现在按 RUNS 现算路径，手工赋值只对本模块的旧读法有效，
    # 漏一个就静默写进真实留档（实测污染过 35 行事件），隔离必须集中一处。
    E.isolate(TMP)

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
    # 直接测**真实的** Daemon._tickets()：总线已 isolate 到 TMP（E.RUNS 就在
    # 临时区），不需要另造扫描根。上一版在这里写了个 _tickets_with_root()
    # **复刻**产品判据来测——那测的是副本：产品里改了规则，复刻不会跟着变，
    # 断言照样绿，等于把最该被钉住的东西放到没人在看的地方。
    # 同时删掉 `d._tickets_roots = lambda: ...`：全仓库没有任何代码读这个
    # 属性（实测 grep 只有这一行赋值），是纯装饰的假接线。
    run_dir = E.RUNS / "patrol_test1"      # ⚠️ 必须在真实扫描根（E.RUNS）下
    # 上一版写成 TMP/"runs"/patrol_test1：isolate(TMP) 后 E.RUNS 就是 TMP，
    # 守护扫的是 TMP/patrol_*，工单却在 TMP/runs/patrol_test1 ⇒ 扫不到，
    # 四条断言里两条直接变红（"停在待批该续跑"、"无决定⇒open"）。
    # 红得明确是好事：改判据前它靠复刻函数掩盖，现在路径一错就露出来。
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
    open_t, go = d._tickets()
    check("已跑完(有verdict)的批准工单 ⇒ 不续跑", go == [])

    # 情形 B：批准过 + 无 verdict + 停在 awaiting_approval ⇒ 该续跑
    (run_dir / "state.json").write_text(json.dumps(
        {"verdict": None,
         "records": [{"kind": "fix", "execute": {"status": "awaiting_approval"}}]}),
        encoding="utf-8")
    open_t, go = d._tickets()
    check("停在待批且无判定 ⇒ 进续跑队列",
          len(go) == 1 and go[0][0] == "patrol_test1")

    # 情形 C：无决定 ⇒ open
    (tk / "decision.json").unlink()
    open_t, go = d._tickets()
    check("无决定的工单 ⇒ open（等，不续跑）",
          len(open_t) == 1 and go == [])

    # 情形 D：有 resume_done 标记 ⇒ 不重复起
    (tk / "decision.json").write_text(
        json.dumps({"decision": "approve"}), encoding="utf-8")
    (tk / "resume_done.json").write_text("{}", encoding="utf-8")
    open_t, go = d._tickets()
    check("已标记 resume_done ⇒ 幂等不重复续跑", go == [])

    # 判据同源（阶段 6）：自检扫盘与守护调度侧必须给出**同一个**待批队列。
    # 两处各写规则时，漂移的表现是"自检说有单、守护说没单"，而排查的人
    # 手里只有这两份输出，对着代码看还都觉得自己对。
    (tk / "decision.json").unlink()
    (tk / "resume_done.json").unlink()
    from upi.agents.daemon import scan_open_tickets
    open_t, _go = d._tickets()
    check("自检扫盘与守护 _tickets 判据完全一致",
          scan_open_tickets() == open_t,
          f"scan={scan_open_tickets()} daemon={open_t}")
    shutil.rmtree(run_dir, ignore_errors=True)   # 本用例夹具收尾：
    # 不删就会有一张"待批工单"留在扫描根里，后续节的 _refresh_status()/
    # UI 渲染用例看到的 open_tickets 就不是它们自己造的那份了（跨用例串味，
    # 表现是别处断言莫名变红，排查方向整个错位）。

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

    print("== ⑤b 告警链路：事件 → status.alerts → UI 常驻可见 ==")
    # 只测"生产者存在"是不够的：alerts 必须在 status 里，UI 必须画出来。
    # 这条链断在任何一环，落盘失败就仍然等于没发生。
    import upi.agents.ui as U
    d5 = _mk_daemon_state({"enabled": True, "interval_s": 1800,
                           "cooldown_s": 0, "auto_fix": {}, "fixtures": []})
    E.emit("state_write_failed", stage="verify", run_id="patrol_demo")
    E.emit("ticket_state_unreadable", ticket="runs/patrol_demo/approvals/T1",
           run_id="patrol_demo", reason="corrupt: Expecting value")
    d5._refresh_status()
    st_al = E.read_status() or {}
    kinds = [a.get("kind") for a in (st_al.get("alerts") or [])]
    check("告警被汇总进 status.alerts",
          "state_write_failed" in kinds and "ticket_state_unreadable" in kinds,
          str(kinds))
    check("degraded 布尔位随告警置真", st_al.get("degraded") is True)
    frame_al = U.snapshot()
    check("UI 帧里出现告警条", "需要注意" in frame_al and "state_write_failed" in frame_al,
          frame_al[frame_al.find("需要注意"):][:120] if "需要注意" in frame_al else "无告警条")
    # 无告警时不该画空条（否则用户分不清"没告警"和"告警没采到"）
    E.emit("daemon_started", pid=os.getpid())
    d5._refresh_status()
    check("alerts 上限 6 条（防长事件流撑爆 status）",
          len((E.read_status() or {}).get("alerts") or []) <= 6)

    print("== ⑥ 命令通道 ack：串行、不卡死、重启丢弃 ==")
    # 单槽通道下"发了"≠"守护看到了"：必须等回执，否则两条命令相互覆盖
    # ⚠️ 用 isolate() 而不是逐条赋常量：总线现在按 RUNS 现算路径
    #   （E.CMD/E.CONF 赋值已失效），本节的重启接管还会扫 runs/_cancel，
    #   RUNS 若指向真实仓库就会动到真实留档。
    E.isolate(TMP)
    seq_a = E.send_cmd("pause")
    check("未回执前 pending=True", E.pending_cmd() is not None
          and E.pending_cmd()["seq"] == seq_a)
    ok_a, info_a = E.send_cmd_and_wait("resume", timeout_s=0.5)
    check("上一条未回执 ⇒ 新命令被拒而非覆盖",
          ok_a is False and info_a["reason"] == "busy")
    check("被拒时留下 command_not_sent（不静默）",
          any(e.get("kind") == "command_not_sent" for e in E.tail_events(4)))
    check("通道文件未被第二条改写（seq 不变）",
          int(E.read_cmd()["seq"]) == seq_a)
    E.ack_cmd(seq_a, cmd="pause")
    check("ack 后 pending 消失", E.pending_cmd() is None)
    # (a) 没有守护在消费（本测试进程）⇒ 必须诚实报"未确认受理"，绝不假装成功
    ok_b, info_b = E.send_cmd_and_wait("resume", timeout_s=0.6)
    check("无人回执 ⇒ 报 no_ack 而不是假装成功",
          ok_b is False and info_b["reason"] == "no_ack", str(info_b))
    check("no_ack 也留痕（发出去了但没人看见）",
          any(e.get("kind") == "command_unacked" for e in E.tail_events(4)))
    # 清掉这条遗留 pending（模拟守护终究会消费），再验有回执的另一条路径
    E.ack_cmd(E.cmd_state()["seq"])
    # (b) 有守护回执 ⇒ acked 成功（后台线程模拟消费，不起真进程）
    import threading
    import time as _time

    ack_stop = [False]

    def _acker():
        seen = int(E.cmd_state().get("acked_seq", 0) or 0)
        while not ack_stop[0]:
            st = E.cmd_state()
            if st["seq"] > seen:
                seen = st["seq"]
                E.ack_cmd(seen, cmd=st["cmd"])
            _time.sleep(0.05)

    th = threading.Thread(target=_acker, daemon=True)
    th.start()
    ok_c, info_c = E.send_cmd_and_wait("run_now", timeout_s=3.0)
    check("有守护回执 ⇒ acked 成功", ok_c is True
          and info_c["reason"] == "acked", str(info_c))
    # ⚠️ 必须关掉模拟回执线程：它是 daemon 线程，留着会在后面的
    #   "重启丢弃"用例里继续 ack，把 pending 态抹平——两个断言同时变红，
    #   看起来像实现的错，其实是测试自己制造的竞态。
    ack_stop[0] = True
    th.join(timeout=2)
    # 守护重启接管：遗留未 ack 命令必须显式丢弃并回执，否则 UI 永久发不出命令
    d2 = _mk_daemon_state({"enabled": True})
    # 造 pending 态：acked 落在 seq 之后（模拟上一代崩在 ack 之前）。
    # 不能靠 ack_cmd 倒退（它取 max），直接写文件——真实形态就是这个。
    pending_seq = E.cmd_state()["seq"] + 1
    E.atomic_write(E._p_cmd(), json.dumps({"seq": pending_seq, "cmd": "run_now",
                                           "args": {}, "ts": E.now(),
                                           "acked_seq": pending_seq - 1}))
    dropped_before = len([e for e in E.tail_events(30)
                          if e.get("kind") == "command_dropped"])
    adopt = d2._adopt_cmd_channel()
    check("重启接管返回当前最大 seq", adopt == pending_seq, str(adopt))
    check("遗留命令被丢弃且留痕（不是静默吞）",
          len([e for e in E.tail_events(8)
               if e.get("kind") == "command_dropped"]) == dropped_before + 1)
    check("丢弃后通道不再 pending（UI 恢复可用）", E.pending_cmd() is None)

    print("== ⑦ 取消通道取代硬杀 + 派生路径可重定向 ==")
    # 真正验"派生"：换一个新的根再 isolate，派生路径必须跟着走。
    # （原先只写 E.RUNS = TMP 且值与上一节相同，等于没测到重定向能力。）
    sub = TMP / "reroot"
    E.isolate(sub)
    check("isolate 后 cancel_dir 跟着新根走（不污染真实 runs）",
          E.cancel_dir() == sub / "_cancel", str(E.cancel_dir()))
    check("isolate 后 decide_path 同样跟着走",
          E.decide_path() == sub / "daemon_decide.json", str(E.decide_path()))
    check("isolate 后事件也写到新根", E.emit("probe-reroot") is not None
          and (sub / "daemon_events.jsonl").exists())
    E.isolate(TMP)                      # 回到主临时区继续后面的用例
    dead = 999997                       # 不存在的 pid：不会真去杀任何进程
    check("request_cancel 落文件并留痕",
          E.request_cancel(dead, reason="user")
          and E.cancel_flag(dead).exists()
          and any(e.get("kind") == "cancel_requested" for e in E.tail_events(3)))
    check("cancel_requested 读回True", E.cancel_requested(dead) is True)
    E.clear_cancel(dead)
    check("clear_cancel 清干净", not E.cancel_requested(dead))
    # 清扫判据：活进程的取消标志**绝不能**按"太旧"删除——那是唯一能
    # 让子进程自己收尾的手段（os.kill 会让它的 finally 不执行）
    E.request_cancel(os.getpid(), reason="orphan-test")
    n_swept = E.sweep_cancel_flags()
    check("活着且持有取消标志 ⇒ 不被清扫（否则孤儿停不下来）",
          n_swept == 0 and E.cancel_requested(os.getpid()) is True)
    check("同时告警孤儿存在",
          any(e.get("kind") == "orphan_child_holding_cancel"
              for e in E.tail_events(3)))
    E.request_cancel(dead, reason="stale")
    check("死进程的残留标志被清走",
          E.sweep_cancel_flags() >= 1 and not E.cancel_requested(dead))
    E.clear_cancel(os.getpid())

    print("== ⑧ 工单租约：崩溃后能自愈 ==")
    tk8 = TMP / "runs" / "patrol_lease" / "approvals" / "APR-1"
    tk8.mkdir(parents=True)
    check("首次抢租约成功", E.acquire_lease(tk8, holder=os.getpid()))
    check("同一持有者未过期 ⇒ 别人抢不到",
          E.acquire_lease(tk8, holder=999999) is False)
    st8 = E.lease_state(tk8)
    check("租约状态可读且指明持有者", st8["held"] is True
          and st8["holder"] == os.getpid())
    # 过期即失效（实测缺口：unlink 之前崩 ⇒ 工单永久卡死）
    (tk8 / E.LEASE_NAME).write_text(json.dumps(
        {"pid": os.getpid(), "ts": "2020-01-01T00:00:00", "ttl_s": 60}),
        encoding="utf-8")
    check("过期租约不再阻塞接手", E.acquire_lease(tk8, holder=os.getpid()))
    # 持有者进程已死 ⇒ 即使没过期也可接手（守护被硬杀的情形）
    (tk8 / E.LEASE_NAME).write_text(json.dumps(
        {"pid": 999996, "ts": E.now(), "ttl_s": 86400}), encoding="utf-8")
    check("持有者已死 ⇒ 租约可被重新取得",
          E.acquire_lease(tk8, holder=os.getpid()) is True)
    E.release_lease(tk8)
    check("release 后文件消失", not (tk8 / E.LEASE_NAME).exists())
    # 半写/损坏的租约文件不得被当成"没人占"（fail-open 会双跑同一工单）
    (tk8 / E.LEASE_NAME).write_text("{坏", encoding="utf-8")
    check("损坏租约按不可判定处理并留痕",
          E.lease_state(tk8)["held"] is False)
    (tk8 / E.LEASE_NAME).unlink()

    print("== ⑨ 决定意向：单一写入者的传输层 ==")
    s1 = E.stage_decide("APR-1", "approve", approver="me", note="ok",
                        token="7f3a")
    check("意向落盘且 seq 自增", s1 == 1 and E.decide_path().exists())
    s2 = E.stage_decide("APR-2", "reject", approver="me", note="no",
                        token="91be")
    check("第二条 seq 递增", s2 == 2)
    got = E.take_decide()
    check("take 读到最新意向", got["ticket"] == "APR-2"
          and got["decision"] == "reject" and got["token"] == "91be")
    check("take 后即清（防重复落盘）", E.take_decide() is None
          and not E.decide_path().exists())

    print("== ⑩ load_json 必须把错误交出来（静默吞异常的源头） ==")
    bad = TMP / "bad.json"
    bad.write_text("{坏行", encoding="utf-8")
    data, err = E.load_json(bad)
    check("坏文件返回 err 且数据为 None", data is None and err.startswith("corrupt"))
    miss, err_m = E.load_json(TMP / "none.json")
    check("缺文件明确报 missing（可与损坏区分）", err_m == "missing")
    # 配置坏掉 ⇒ 留痕后回落，不静默当空配置（用户以为关掉的采样会继续跑）
    # ⚠️ 必须写派生路径 E._p_conf()：总线现在按 RUNS 现算路径，
    #   再赋 E.CONF 常量已经不影响 load_conf/save_conf 的取数（实测两条断言
    #   因此变红——是实现错还是测试错？是实现按 I9 现算、测试还留着旧写法）。
    conf_p = E._p_conf()
    conf_p.write_text("{坏", encoding="utf-8")
    check("坏配置 load 返回空 dict", E.load_conf() == {})
    check("同时发 conf_unreadable 留痕",
          any(e.get("kind") == "conf_unreadable" for e in E.tail_events(4)))
    conf_p.unlink()
    check("save_conf 返回落盘结果", E.save_conf({"enabled": False}) is True
          and E.load_conf()["enabled"] is False)

    # ── 配置源递归合并（阶段 5 引入嵌套配置后暴露的真缺陷回归）──
    # 覆盖层现在会写 episode.heartbeat_max_age_s / budget.max_review_rounds
    # 这类**子键**。浅合并 {**base, **ov} 时 ov["episode"] 只含一个子键也会
    # 整个顶掉 base["episode"]，探针路径 jsonl/heartbeat 就此消失 ⇒ 发作通路
    # 静默变盲区，而且 patrol_episode 的留痕谎报"探针没布置"，引着人去重布
    # 一套本来好好的探针。旧代码只给 auto_fix 开后门做深合并，补一个漏两个。
    _b = {"episode": {"jsonl": "a.jsonl", "heartbeat": "hb",
                      "heartbeat_max_age_s": 12},
          "auto_fix": {"enabled": True, "live_llm": False,
                       "fixtures": ["F-CPU-01"]},
          "fixtures": ["A", "B"], "interval_s": 1800}
    _o = {"episode": {"heartbeat_max_age_s": 30},
          "auto_fix": {"live_llm": True},
          "fixtures": ["C"]}
    _m = D._deep_merge(_b, _o)
    check("子字典递归并：改心跳年龄不抹掉探针路径",
          _m["episode"].get("jsonl") == "a.jsonl"
          and _m["episode"].get("heartbeat") == "hb"
          and _m["episode"].get("heartbeat_max_age_s") == 30, str(_m["episode"]))
    check("auto_fix 同样递归（不给单个键开后门）",
          _m["auto_fix"] == {"enabled": True, "live_llm": True,
                             "fixtures": ["F-CPU-01"]}, str(_m["auto_fix"]))
    check("list 整体替换而非逐项并（避免删了又被加回的中间态）",
          _m["fixtures"] == ["C"], str(_m["fixtures"]))
    check("未被覆盖的标量保持打底值", _m["interval_s"] == 1800,
          str(_m["interval_s"]))
    _ep, _af = _o["episode"], _o["auto_fix"]
    check("合并不改动入参（覆盖层对象别处还会用到）",
          _b["episode"]["heartbeat_max_age_s"] == 12
          and _ep == {"heartbeat_max_age_s": 30}
          and _af == {"live_llm": True}, f"{_b['episode']} {_ep} {_af}")
    check("打底缺子字典 ⇒ 覆盖层照常落位",
          D._deep_merge({}, {"budget": {"max_review_rounds": 2}})
          == {"budget": {"max_review_rounds": 2}}, "空打底")
    check("类型冲突按覆盖层替换（合并自身不许崩）",
          D._deep_merge({"x": 1}, {"x": {"a": 2}}) == {"x": {"a": 2}}, "冲突")

    # 真链路：走 set 分支改嵌套子键 ⇒ 覆盖层只含改动项，重启合并后
    # 兄弟键（探针路径）仍在。这条才是要害——上面几条只证明函数本身对。
    _live = {"interval_s": 1800,
             "episode": {"jsonl": "runs/_probe/pb_breach.jsonl",
                         "heartbeat": "runs/_probe/pb_hb.txt",
                         "heartbeat_max_age_s": 12}}
    _d = _mk_daemon_state(_live)
    _d._dispatch_cmd({"cmd": "set", "args": {"episode_heartbeat_max_age_s": 30}})
    _ov = E.load_conf()
    check("set 只把改动子键写进覆盖层",
          _ov.get("episode") == {"heartbeat_max_age_s": 30}, str(_ov))
    _re = D._deep_merge(_live, _ov)
    check("重启合并后探针通路仍可用",
          all(k in _re["episode"] for k in ("jsonl", "heartbeat"))
          and _re["episode"]["heartbeat_max_age_s"] == 30, str(_re["episode"]))
    E._p_conf().unlink(missing_ok=True)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n==== 守护层离线测试: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
