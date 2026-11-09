"""控制台 UI（零依赖 ANSI，不新增第三方包）。

形态：全屏定时刷新 + 单键控制。渲染的所有内容都从文件读（events/status/
conf/tickets），UI 不持有自己的状态——界面崩了不影响守护，守护崩了界面
会如实显示"心跳过期"而不是继续演。

交互规则（阶段 4）：
  指令菜单**按当前状态动态给出**（状态从 daemon_status.json + 工单派生，
  UI 不自造状态）。不可用的指令**显示但置灰并附原因**——藏起来会让人以为
  功能缺失，下次在错误的态里按它更摸不着头脑。
  确认强度三档：只读按了就走；可逆（开关/暂停/巡检/取消）需再按一次确认；
  不可逆（停守护/注册/批准）需手输 token。肌肉记忆是审批的头号敌人。
  键位含义见每帧底部实时菜单，本文不重复列举（列举就会腐坏，历史上已错过）。

非 TTY（管道/重定向）自动退化成一次性快照打印，便于脚本与 CI 取用。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from upi.agents import ui_actions as UA  # noqa: E402
from upi.config.runtime_spec import SPEC  # noqa: E402
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

    # ── 常驻告警条 ──
    # 告警必须"一直看得见"：事件流只显示最近 11 条，一条 write_failed 会被
    # 后续 progress 事件挤出屏幕。落盘失败/坏文件这类事一旦看不见，
    # 就等于告诉用户"一切正常"——这正是本阶段要消灭的形态。
    al = (st or {}).get("alerts") or []
    if al:
        out.append(f"{RED}▲ 需要注意 {len(al)} 条告警（最新 3 条）{RESET}")
        for a in al[:3]:
            out.append(f"   {RED}{a.get('kind')}{RESET} "
                       f"{DIM}{a.get('ts')} {str(a.get('what') or a.get('detail') or '')[:70]}{RESET}")

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

    # ── 动态指令菜单（按状态给可用项；禁用项置灰，原因在按下时给出）──
    state = UA.derive_state(st, calibrated=UA.is_calibrated())
    acts = UA.actions_for(state)
    on = [a for a in acts if a["enabled"]]
    off = [a for a in acts if not a["enabled"]]
    out.append(f"{BOLD}指令{RESET} {DIM}[状态：{state}]{RESET}")
    out.append("  " + "  ".join(f"{GREEN}[{a['key']}]{RESET}{a['label']}"
                                for a in on))
    if off:
        # 显示禁用项但不写全原因（原因可能很长）：按下去时 check_guard 会把
        # 确切原因打到屏幕上，这里只标"为什么整片不可用"的态名。
        out.append(f"  {DIM}置灰不可用：{'  '.join('[' + a['key'] + ']' + a['label'] for a in off)}{RESET}")
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


def send(cmd: str, **args) -> tuple[bool, str]:
    """发命令并等守护回执。返回 (是否受理, 一行人可读的结果)。

    为什么必须等：命令通道只有一个槽位。不等就会出现"两条命令相互覆盖、
    后写的赢"——前一条按下去毫无反应也没人报错（阶段 1 要堵的洞）。
    这里不假装成功：没回执就说没回执，让人知道该再看一眼还是该重启守护。
    """
    ok, info = E.send_cmd_and_wait(cmd, **args)
    if not ok:
        if info.get("reason") == "busy":
            b = info.get("blocked_by") or {}
            return False, (f"未发出：上一条命令（{b.get('cmd')}，seq={b.get('seq')}）"
                           "守护还没受理完，请稍候再按")
        return False, f"未确认受理（seq={info.get('seq')}）：守护可能已停止，请按 s 查看状态"
    return True, f"已受理：{cmd}（seq={info.get('seq')}）"


def _ticket_of(st_now: dict) -> tuple[Path | None, str]:
    """从状态快照取当前待批工单，返回 (工单目录, request_id)。

    取 open_tickets 的**最后一条**（与 view_latest_ticket 同口径）。守护在
    AWAITING 态本就不叠加新轮、一次只留一张，所以这里不做"多张里挑"。
    """
    ot = (st_now or {}).get("open_tickets") or []
    if not ot:
        return None, ""
    p = Path(ot[-1])
    return (p.parent if p.exists() else None), p.parent.name


def approve_flow(st_now: dict, line_read, token_input=None) -> str:
    """工单审批闭环（阶段 5）：逐页强制读完 → 决定 → 页脚码手输 → 提交意向。

    为什么强制分页读完才给批：工单是"五件套"（证据/方案/预期/回滚/失败预案），
    漏看任何一件都可能批一个自己没理解的动作。计划把这条写成硬判据
    （"未读全则批准键不可用"），所以这里用"逐页回车 + 末页才印校验码"落实，
    不靠人自觉。校验码只依赖工单号 ⇒ 与 md 全文无循环定义，守护能独立复算。
    """
    from upi.core import approval as AP

    d, rid = _ticket_of(st_now)
    if d is None:
        return "当前没有可批的工单（或工单文件已不存在）"
    # 短路：本单已有生效决定（上一次的意向刚被守护落盘）就不再走阅读流程。
    # 少了这一步，"重复按 d"会把人带进一遍完整五页再在 token 处被守护拒掉
    # （同 request_id 已写过 decision），看起来像审批坏了。
    from upi.core import approval as _AP
    _existing = _AP.ApprovalStore(d.parents[1]).load_decision(rid)
    if _existing is not None:
        return (f"{rid} 已有决定：{_existing.decision}"
                f"（{_existing.approver}，经 {_existing.decided_via}），"
                "审批通道已关闭，无需重复操作")
    md_path = d / "request.md"
    try:
        md = md_path.read_text(encoding="utf-8")
    except OSError as e:
        # 读不到正文就绝不进入"可以批"的分支——宁可不批，不可假批
        return f"工单正文读取失败，未进入审批：{type(e).__name__}: {str(e)[:80]}"

    pages = AP.split_pages(md)
    tip = "（回车读下一页）"
    for i, seg in enumerate(pages, 1):
        body = seg if len(seg) <= 4000 else seg[:4000] + "\n…（本页过长已截断）"
        got = line_read(f"—— 工单 {rid} 第 {i}/{len(pages)} 页 ——\n{body}\n{tip}")
        if (got or "").strip().lower() in ("q", "esc", "quit"):
            return f"已中止阅读，未提交任何决定（读到第 {i}/{len(pages)} 页）"
    # 读完全文才走到这里。校验码就在第 5 页页脚，抄得到说明翻到了底。

    dec_raw = (line_read("决定：a=批准 / r=驳回 / d=暂缓（其他键=放弃本次审批）：")
               or "").strip().lower()
    verdict = {"a": "approve", "r": "reject", "d": "defer"}.get(dec_raw)
    if verdict is None:
        return "未做出决定（需 a/r/d），工单仍待批"
    who = (line_read("批准人（你的名字，将写入留痕并按审批者分层统计）：") or "").strip()
    note = (line_read("批语：批/驳的具体内容与理由（approve 必须非空）：") or "").strip()
    if verdict == "approve" and not (who and note):
        # 前端先挡一次，后端 ApprovalDecision 有同样规则（双闸同源，不互信）
        return "批准必须同时给出批准人与批语（缺一视同未批），本次未提交"

    tok = (token_input or line_read)(
        f"请输入工单页脚的 4 位校验码（证明已读完 {len(pages)} 页）：")
    if not AP.verify_token(rid, tok or ""):
        return (f"校验码不匹配，未提交（工单 {rid} 仍待批）。"
                "码印在工单末页页脚——没读到最后就抄不到它。")

    # 只交意向：decision.json 必须经守护的单一写入路径落盘（含回读确认）。
    # UI 直接写文件等于绕过校验与留痕，审批链就不可信了。
    E.stage_decide(str(md_path), verdict, approver=who, note=note,
                   token=(tok or "").strip())

    # 等到守护真的把它落成 decision.json 才报"已生效"——只报"意向已交"
    # 会把"守护还没跑/崩了"显示成成功（与 start_daemon 等心跳同一教训）。
    store = AP.ApprovalStore(d.parents[1])
    for _ in range(int(DECIDE_WAIT_S * 10)):
        if store.load_decision(rid) is not None:
            return (f"{rid} 的决定已生效：{verdict}（批准人 {who}）"
                    "｜批准将触发续跑，进度见上方状态")
        time.sleep(0.1)
    return (f"意向已提交，但 {DECIDE_WAIT_S:.0f}s 内守护未落盘"
            f"（{rid} 仍显示待批）——请确认守护在跑，必要时按 b 启动")


DECIDE_WAIT_S = 20.0      # 等守护落 decision.json 的上限，与一个心跳周期同量级


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


def start_daemon() -> str:
    """UI 内拉起守护（阶段 6 会加开机自启，这里先把'起来'这件事收进界面）。

    复用 cmd_start 的两道防呆：① 已有守护就拒绝（双判据）；② 必须真的
    看到心跳才报成功——否则会把"还在起进程"报成"启动成功"（假安心）。
    """
    if E.daemon_running()[0]:
        return "守护已在运行，无需重复启动"
    import subprocess
    import sys as _sys
    # 路径用本模块的 ROOT（ui.py 顶部已定义）。此处曾写成
    # `from upi.__main__ import ROOT`——那是多余的绕路，而且有真风险：
    # 用户跑 `python -m upi ui` 时 __main__ 正在执行中，从它 import
    # 会撞上半初始化模块（循环导入）。取常量不该跨模块绕。
    log = open(ROOT / "runs" / "daemon_stdout.log", "ab", buffering=0)
    flags = 0x00000008 | 0x00000200 if _sys.platform == "win32" else 0
    try:
        subprocess.Popen([_sys.executable, "-m", "upi", "monitor"],
                         cwd=str(ROOT), stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as e:
        return f"启动失败：{e}"
    for _ in range(12):
        time.sleep(1)
        running, st = E.daemon_running()
        if running:
            return f"守护已启动 pid={st.get('pid')}"
    return "!! 12 秒内未见心跳，启动可能失败（查 runs/daemon_stdout.log）"


# 可编辑的运行配置：与守护 _dispatch_cmd 的 set 分支**逐键对应**。
# 可改的运行配置：字段、中文名、类型、上下界全部来自 runtime_spec.SPEC。
# ⚠️ 这里绝不再手写一份字段清单——守护 set 分支、状态导出 conf 也各读同一份
# SPEC。三处曾各写各的，漏改任意一处的后果都是"界面显示改成功了、其实守护
# 按未知字段静默丢弃"（最难查的假接线）。加配置项只动 SPEC 一个地方。
CFG_FIELDS = [(i.key, i.label, i.kind) for i in SPEC]


def _cfg_current(st_now: dict):
    """读当前配置值（展示用，取不到就 None，不编一个数）。

    守护把 conf 按 SPEC 扁平导出（{key: 值}），这里直接按 key 取即可，
    不需要再关心 live_llm 藏在 auto_fix 子字典这类落盘结构——那是 SPEC.path
    的职责，UI 不该重复知道。
    """
    conf = (st_now or {}).get("conf") or {}
    return {i.key: conf.get(i.key) for i in SPEC}


def edit_config(st_now: dict, send_, line_read) -> str:
    """一次改一个运行配置项（数值/开关）。取消不改变任何东西。

    为什么不让用户一次填一整张表：改配置直接影响成本（间隔、窗口数决定
    每轮花多久花多少积分），逐项确认 + 立即回显 old→new 才敢让人改。
    """
    cur = _cfg_current(st_now)
    n = len(SPEC)
    lines = ["可改的运行配置（回车取消，不改任何东西）："]
    for i, item in enumerate(SPEC, 1):
        v = cur.get(item.key)
        shown = "未设" if v is None else v
        rng = "" if item.kind is bool else f"（{item.lo}{item.unit}~{item.hi}{item.unit}）"
        lines.append(f"  {i}. {item.key}  {item.label}  当前={shown}{rng}")
    lines.append("")
    pick = line_read("\n".join(lines) + f"\n选哪项(1-{n})：")
    sel = (pick or "").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= n):
        return f"未选择有效项（需在 1-{n}），配置未改动"
    item = SPEC[int(sel) - 1]
    old = cur.get(item.key)
    prompt = (f"给 {item.key}（{item.label}）填新值"
              f"{'，y/n' if item.kind is bool else f'，{item.lo}{item.unit}~{item.hi}{item.unit}'}"
              "，回车取消：")
    raw = (line_read(prompt) or "").strip()
    if not raw:
        return f"已取消，{item.key} 仍为 {old}"
    # 边界与类型校验直接走 SPEC.cast（与守护同一道闸）：UI 这关挡下越界，
    # 就不用发一条守护必然回绝的命令、让人只看到一句"未确认"摸不着头脑。
    try:
        val = item.cast(raw)
    except (ValueError, TypeError) as e:
        return f"输入无效：{e}；{item.key} 未改动"
    ack, info = send_("set", **{item.key: val})
    tag = "" if ack else "（守护未确认受理，见告警）"
    return f"{item.key}: {old} → {val}{tag}"



def step(state: str, st_now: dict, key: str, pending=None, *,
         sender=None, runner=None, token_input=None, line_reader=None,
         calib=True):
    """处理一次按键，返回 (提示文本, 新的待确认动作, 是否退出)。

    为什么单独成一个函数而不是留在 while 里：交互规则（白名单、禁用原因
    回显、三档确认）是这套 UI 最容易出错的部分——状态误判会让人在错误的
    时刻发出破坏性命令。逻辑埋在渲染循环里就只能真开终端手点才能验，
    手点测不到"拒绝路径"。抽出来后测试可以直接喂一串按键断言每一步。

    依赖全部以参数注入（sender/runner/token_input），默认走真实通道：
    测试因此能断言"到底发了哪条命令"，而不是只看界面文字。
    """
    send_ = sender or send
    run_ = runner if runner is not None else start_daemon
    # 读一行统一走 line_read：配置表单要读两次（选项 + 新值），token 确认读一次。
    # 注入而非直接 input()，是为了让交互逻辑能被测试驱动——真终端手点测不到
    # 拒绝路径（取消、越界、类型不符）。
    line_read = line_reader or (lambda prompt: (sys.stdout.write(prompt),
                                               input())[-1])
    tin = token_input or line_read

    def out(msg, pend=None, quit_=False):
        return msg, pend, quit_

    act = UA.resolve(state, key)
    if act is None:
        # 未识别键：作废待确认（否则下一次按键会被当成它的确认而误执行）
        return out(f"未识别按键 {key!r}", None)

    allowed, why = UA.check_guard(state, act, st_now)
    if not allowed:
        # 禁用原因必须回显——"显示并置灰"只做了一半，另一半是让人知道为什么
        return out(f"{act['label']} 当前不可用：{why}", None)

    if act["tier"] == UA.TIER_HARD and not act.get("wired", True):
        # 未接线动作在这里再拦一次：actions_for 已置灰，但矩阵若被改坏，
        # 这里不兜住就会把"看起来能用其实必然失败"的动作真的执行
        return out(f"{act['label']} 尚未实现", None)

    if act["tier"] == UA.TIER_SOFT:
        # 软确认用"再按一次同一键"，不弹文字输入框：控制台是全屏刷新的，
        # 中途弹 input 会和渲染打架（也测不到）。只有不可逆动作（TIER_HARD）
        # 才临时让出屏幕收一行 token。
        if pending == act["name"]:
            pass                              # 二次按键 = 确认，继续执行
        else:
            hint = (f"⚠ 待确认「{act['label']}」：再按 [{act['key']}] 生效，"
                    "按其他键作废")
            return out(hint, act["name"])

    if act["name"] == UA.ACT_DECIDE:
        # 审批自带硬确认（读毕 5 页 + 手输页脚 4 位码），所以走 TIER_HARD
        # 之前特判掉通用 token：两层确认不仅冗余，通用 token（每张单都是
        # "D"）还是更弱的那层——留着它反而稀释"读毕码"的注意力。
        # 误触 [d] 无副作用：流程第一步只是开始读第 1 页，随时可中止。
        return out(approve_flow(st_now, tin, token_input=tin), None)

    if act["tier"] == UA.TIER_HARD:
        tok = act["key"].upper()
        try:
            got = tin(f"不可逆操作「{act['label']}」，请输入 {tok} 后回车：")
        except (EOFError, KeyboardInterrupt):
            return out("已取消该操作", None)
        if (got or "").strip() != tok:
            return out(f"token 不匹配，未执行「{act['label']}」", None)

    # 放行执行
    if act["name"] == UA.ACT_QUIT_UI:
        return out("", None, True)
    if act["name"] == UA.ACT_START_DAEMON:
        return out(run_(), None)
    if act["name"] == UA.ACT_VIEW_TICKET:
        return out(view_latest_ticket(), None)
    if act["name"] == UA.ACT_SNAPSHOT:
        return out("已刷新（本帧即快照）", None)
    if act["name"] == UA.ACT_TOGGLE_SAMPLING:
        return out(send_("disable" if st_now.get("sampling_enabled")
                         else "enable")[1], None)
    if act["name"] == UA.ACT_CONFIG:
        return out(edit_config(st_now, send_, line_read), None)
    if act["name"] == UA.ACT_CANCEL:
        # 取消粒度是"下一个采样边界"，不是瞬时停：走文件通道才换得来子进程
        # 的 finally 真执行（退 Play、卸回调、写完自己的留痕）。这句话必须说
        # ——否则按下 c 之后界面看似毫无反应，人只会连按几次，或者干脆去
        # 硬杀进程，而那正是 G10 禁止的（会连带关掉别的项目）。
        # GRACE_S 是父进程侧的强杀兜底上界，所以"最长"是有依据的数字，不是安慰话。
        from .patrol import GRACE_S
        okc, msg = send_("cancel")
        return out(msg + ("" if not okc else
                          f"｜已受理，等下一个采样边界收尾（最长 {GRACE_S}s）"),
                   None)
    if act.get("cmd"):
        return out(send_(act["cmd"])[1], None)
    return out(f"动作 {act['name']} 未实现（不应被放行）", None)


def run_loop(fps: float = 2.0) -> int:
    if not sys.stdout.isatty():
        print(snapshot())
        return 0
    vt = enable_vt()
    toast = ""
    pending = None                                  # 待软确认的动作名
    try:
        while True:
            st_now = E.read_status() or {}
            state = UA.derive_state(st_now, calibrated=UA.is_calibrated())
            frame = snapshot()
            sys.stdout.write("\x1b[2J\x1b[H" if vt else "\n" * 2)
            sys.stdout.write(frame + "\n")
            line = toast
            if not line and pending:
                # 待确认提示由 pending 反查键名，不硬编码——菜单是动态的
                line = (f"⚠ 待确认「{_label_of(pending)}」："
                        f"再按 [{_key_of(pending)}] 生效，按其他键作废")
            if line:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
            key = read_key(1.0 / fps)
            if not key:
                continue
            toast, pending, should_quit = step(state, st_now, key, pending)
            if should_quit:
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write(RESET + "\n")


def _key_of(action_name: str) -> str:
    for a in UA._ALL_ACTIONS:
        if a["name"] == action_name:
            return a["key"]
    return ""


def _label_of(action_name: str) -> str:
    """动作名 → 中文标签。菜单与待确认提示都只显示标签，不暴露内部名。"""
    for a in UA._ALL_ACTIONS:
        if a["name"] == action_name:
            return a["label"]
    return action_name


if __name__ == "__main__":
    sys.exit(run_loop())
