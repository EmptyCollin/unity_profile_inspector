"""控制台 UI（零依赖 ANSI，不新增第三方包）。

形态：全屏定时刷新 + 单键控制。渲染的所有内容都从文件读（events/status/
conf/tickets），UI 不持有自己的状态——界面崩了不影响守护，守护崩了界面
会如实显示"心跳过期"而不是继续演。

键位：
  s  开/关采样     p 暂停/继续      r 立即巡检一次
  c  取消当前轮    a 打开待批工单   q 退出（只退 UI，不停守护）

非 TTY（管道/重定向）自动退化成一次性快照打印，便于脚本与 CI 取用。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from upi.core import events as E  # noqa: E402

RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"
GREEN, RED, YELLOW, CYAN = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[36m"
BG_GREY = "\x1b[48;5;236m"


def enable_vt() -> bool:
    """Windows legacy console 默认不解析 ANSI，开 VT 处理模式。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:  # noqa: BLE001
        return False


def read_key(timeout: float) -> str:
    """非阻塞取键。Windows 用 msvcrt，POSIX 用 tty raw 单字符。"""
    if os.name == "nt":
        import msvcrt
        if msvcrt.kbhit():
            return msvcrt.getwch().lower()
        return ""
    import select
    import termios
    import tty
    try:
        fd = sys.stdin.fileno()
    except (ValueError, OSError):
        time.sleep(timeout)
        return ""
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1).lower() if r else ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _rel(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return ts
    d = int(time.time() - t)
    if d < 0:
        return f"还有 {-d // 60}分{-d % 60}秒"
    return f"{d // 60}分{d % 60}秒前" if d < 3600 else f"{d // 3600}时前"


def _hb_age(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    return int(time.time() - t)


def snapshot() -> str:
    """一帧全屏文本（也可单独用于非 TTY 一次性输出）。"""
    running, st = E.daemon_running()
    pg = None
    try:
        pg = json.loads((ROOT / "runs" / "daemon_progress.json")
                        .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    out: list[str] = []
    W = 74
    out.append(f"{BOLD}{BG_GREY} UPI 性能监测 Agent · 控制台 {RESET}"
               f"{DIM}{'·' * max(0, W - 32)}{RESET}")

    # ── 状态条：存活判据必须能看清依据（pid + 心跳双判据）──
    hb = _hb_age((st or {}).get("updated_at"))
    if running:
        dot = f"{GREEN}● 运行中{RESET}"
    elif st and hb is not None and hb > 15:
        dot = f"{RED}● 已停止{RESET} {DIM}心跳过期 {hb}s{RESET}"
    elif st:
        dot = f"{RED}● 已停止{RESET} {DIM}pid 不存活{RESET}"
    else:
        dot = f"{RED}● 未启动{RESET}"
    sampling = (st or {}).get("sampling_enabled")
    phase = (st or {}).get("phase", "-")
    out.append(f" 守护 {dot}  pid={(st or {}).get('pid','-')}  "
               f"采样 {'{0}{1}开{2}'.format(GREEN, '●', RESET) if sampling else '{0}{1}关{2}'.format(DIM, '○', RESET)}  "
               f"状态 {phase}")

    # ── 进度：当前在采哪个夹具第几窗口 ──
    if pg and pg.get("alive") and pg.get("stage") != "write_bundle":
        win = f" 窗口 {pg.get('window')}/{pg.get('windows_total')}" \
            if pg.get("window") else ""
        ft = pg.get("fixture") or ""
        out.append(f"{CYAN}▸ 进行中{RESET} {ft}{win} "
                   f"{DIM}({pg.get('stage')}) {pg.get('note','')}{RESET}")
        bar_w = 40
        # 进度按"夹具序×窗口"累积：每个夹具都从窗口 1 重来，只取当前 window
        # 会让进度条每换一个夹具就跳回低位，读不出"整体跑到哪"。用 fixture
        # 在列表里的序号折算全局完成数，条子才单调递增。仍是估算，如实标注。
        fixtures_list = (st or {}).get('conf', {}).get('fixtures') or []
        wtot = int(pg.get("windows_total") or 1)
        fidx = fixtures_list.index(ft) if ft in fixtures_list else 0
        done = fidx * wtot + (pg.get("window") or 0)
        tot = max(1, len(fixtures_list) * wtot)
        filled = int(bar_w * min(1.0, done / tot)) if tot else 0
        out.append(f"  {'█' * filled}{'░' * (bar_w - filled)} "
                   f"{DIM}按窗口估算{RESET}")
    else:
        nxt = (st or {}).get("next_round_at")
        out.append(f"{DIM}▸ 空闲{RESET}  "
                   f"{'下次轮次 ' + _rel(nxt) if nxt else '尚未排期'}")

    # ── 计数 ──
    c = (st or {}).get("counters") or {}
    last = ((st or {}).get("last_round") or {})
    if last:
        verdict = ("静默" if last.get("quiet") else
                   (f"检出 {last.get('findings')} 条" if last.get("findings")
                    else "未评估"))
        bs = last.get("blindspots") or []
        out.append(f" 轮次累计 {c.get('rounds', 0)}  · 最近一轮 {verdict} "
                   f"{DIM}({last.get('seconds','-')}s){RESET}"
                   + (f" {YELLOW}盲区 {len(bs)} 项{RESET}" if bs else ""))
        for e_ in (last.get("wakes") or []):
            if e_.get("ticket"):
                out.append(f"   ↳ {e_.get('fixture')} 已出工单 "
                           f"{DIM}{e_['ticket']}{RESET}")
            elif not e_.get("woken"):
                out.append(f"   ↳ {e_.get('fixture')} 未唤起 "
                           f"{DIM}({e_.get('why')}){RESET}")
    if last.get("error"):
        out.append(f"  {RED}轮次失败:{RESET} {str(last['error'])[:88]}")

    # ── 待批工单 ──
    ot = (st or {}).get("open_tickets") or []
    if ot:
        out.append(f"{YELLOW}⚠ 待你决定的工单 {len(ot)} 张{RESET}"
                   f"（守护在等，未批不采基线）")
        for p in ot[:3]:
            out.append(f"   {p}")
        out.append(f"   {DIM}按 a 查看最新一张；批了写 decision.json 即可，"
                   f"守护自动接着跑完修复+验证{RESET}")
    else:
        out.append(" 待批工单 无")

    # ── 事件流 ──
    evs = E.tail_events(11)
    out.append(f"{BOLD}事件流{RESET}")
    KNOWN = {"daemon_started": CYAN, "daemon_stopped": RED,
             "round_started": CYAN, "round_finished": GREEN,
             "round_error": RED, "round_skipped": DIM,
             "ticket": YELLOW, "resume_started": CYAN,
             "resume_finished": GREEN, "resume_failed": RED,
             "sampling_enabled": GREEN, "sampling_disabled": YELLOW,
             "command_received": DIM, "command_ignored": RED,
             "cancel_sent": YELLOW, "progress": DIM}
    for ev in evs:
        k = ev.get("kind", "?")
        col = KNOWN.get(k, "")
        detail = _fmt_ev(ev)
        out.append(f"  {DIM}{ev.get('ts','')[11:]}{RESET} {col}{k:<17}{RESET} {detail}")

    out.append(f"{BOLD}{BG_GREY} s开关采样  p暂停  r立即巡检  c取消本轮  a看工单  q退出{RESET}")
    return "\n".join(out)


def _fmt_ev(ev: dict) -> str:
    k = ev.get("kind")
    if k == "progress":
        return f"{ev.get('fixture','')} {ev.get('stage','')} " \
               f"{ev.get('window') or ''}/{ev.get('windows_total') or ''}"
    if k == "round_finished":
        return f"{ev.get('seconds')}s 检出{ev.get('findings')} 静默{ev.get('quiet')}"
    if k == "round_started":
        return f"触发={ev.get('trigger')} 采样={ev.get('sampling')}"
    if k == "ticket":
        return f"{ev.get('fixture')} → {Path(str(ev.get('md',''))).parent.name}"
    if k in ("resume_started", "resume_finished", "resume_failed"):
        return f"{ev.get('run_id','')} {ev.get('status','')} " \
               f"{str(ev.get('error',''))[:50]}"
    if k == "waiting_on_decision":
        return f"{len(ev.get('tickets') or [])} 张待批"
    if k == "command_ignored":
        return f"{ev.get('cmd')} 原因={ev.get('reason')}"
    if k == "round_error":
        return str(ev.get("error", ""))[:70]
    if k == "config_changed":
        return " ".join(f"{a}={b}" for a, b in ev.items()
                        if a not in ("ts", "kind"))
    return " ".join(f"{a}={str(b)[:24]}" for a, b in ev.items()
                    if a not in ("ts", "kind"))[:80]


def send(cmd: str, **args) -> None:
    E.send_cmd(cmd, **args)
    # 立即回显一条"已发出"，让按键有反馈；守护消费时会再发 command_received
    E.emit("command_queued", cmd=cmd, args=args)


def view_latest_ticket() -> str:
    st = E.read_status() or {}
    ot = st.get("open_tickets") or []
    if not ot:
        return "当前没有待批工单"
    p = Path(ot[-1])
    if not p.exists():
        return f"工单文件不存在：{p}"
    try:
        import webbrowser
        webbrowser.open(p.resolve().as_uri())
        return f"已在系统查看器打开：{p.name}"
    except Exception:  # noqa: BLE001
        return f"请手工打开：{p}"


def run_loop(fps: float = 2.0) -> int:
    if not sys.stdout.isatty():
        print(snapshot())
        return 0
    vt = enable_vt()
    try:
        while True:
            frame = snapshot()
            # 清屏 + 归位；无 VT 支持时退化为逐帧追加（可读但不美观）
            sys.stdout.write("\x1b[2J\x1b[H" if vt else "\n" * 2)
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()
            key = read_key(1.0 / fps)
            if key == "q":
                return 0
            elif key == "s":
                st = E.read_status() or {}
                send("disable" if st.get("sampling_enabled") else "enable")
            elif key == "p":
                st = E.read_status() or {}
                send("resume" if st.get("paused") else "pause")
            elif key == "r":
                send("run_now")
            elif key == "c":
                send("cancel")
            elif key == "a":
                print("\n" + view_latest_ticket())
                read_key(3.0)
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write(RESET + "\n")


if __name__ == "__main__":
    sys.exit(run_loop())
