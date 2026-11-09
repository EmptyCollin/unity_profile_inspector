"""守护进程的事件总线：进度事件流、状态快照、控制命令通道、工单租约与取消通道。

设计四条（都是本仓实测教训的平移）：
- 一切走文件且原子写（tmp+replace）——守护、控制台、CLI 是三个独立进程，
  内存态不共享也不可信（I9 的同型纪律）；
- 进度必须**可观测**：没检出的轮次、被拒的命令、进行到哪个夹具哪个窗口，
  都要能从事件流里读出来。"在跑"与"睡着"必须分得清（I10 的交互层版本）；
- 命令带 seq 去重：控制台崩了重发同一条不能二次生效；守护重启后旧命令
  不被重新消费（processed_seq 落状态文件）；
- **写入方必须显式处置失败**（v0.15 新增契约）。本模块不再提供"读失败就返回
  None/空 dict"的便利函数给业务层用：`load_json` 返回 (数据, 错误) 二元组，
  调用方拿到非空 err 就必须 emit 或降级，静默 `pass` 无处可藏。
  起因（实测）：`state.json` 每节点直接 write_text 且 `except OSError: pass`
  丢盘零留痕，而守护的续跑判据 `_tickets()` 正依赖该文件——坏盘一次，
  那张工单就永久不再续跑且无人知晓。同理 CMD 的 seq 读失败曾被 `except: pass`
  当成 seq=0 处理，会让新命令与旧命令同号而被守护跳过。

文件布局（除注明外全在 runs/ 下）：
  daemon_events.jsonl  进度/决定事件流（append-only）
  daemon_status.json   当前快照（原子覆盖：阶段、进度、计数、下次轮次）
  daemon_cmd.json      控制命令（最新一条，seq 去重消费 + acked_seq 回执）
  daemon_conf.json     运行时配置（UI 改的开关活过重启，优先于 llm_config）
  daemon_decide.json   UI 暂存的工单决定意向（由守护落 decision.json，单一写入者）
  _cancel/<pid>        取消通道（采样子进程在窗口边界自查并优雅收尾）
  daemon.pid           守护进程 PID
  ../projects/         ProjectProfile 注册表（工程协作配置，阶段 7 消费）
  工单目录内           resume_lease.json（续跑租约，防半途崩后永久卡单）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
EVENTS = RUNS / "daemon_events.jsonl"
STATUS = RUNS / "daemon_status.json"
CMD = RUNS / "daemon_cmd.json"
CONF = RUNS / "daemon_conf.json"
PID = RUNS / "daemon.pid"
CANCEL_SUBDIR = "_cancel"
DECIDE_NAME = "daemon_decide.json"
PROJECTS = ROOT / "projects"


# ─────────────── 路径解析与测试隔离 ───────────────
# ⚠️ 所有函数内部**必须调用下面的 _p_*() 而不是直接用模块常量**：
#   测试只重定向 E.RUNS 时，若函数体仍引用 EVENTS/STATUS 等常量，
#   留痕照样写进真实 runs/（本轮实测污染了 daemon_events.jsonl 35 行）。
#   常量保留只为兼容既有读法，函数取路径一律现算。
def _p_events() -> Path:
    return RUNS / "daemon_events.jsonl"


def _p_status() -> Path:
    return RUNS / "daemon_status.json"


def _p_cmd() -> Path:
    return RUNS / "daemon_cmd.json"


def _p_conf() -> Path:
    return RUNS / "daemon_conf.json"


def _p_pid() -> Path:
    return RUNS / "daemon.pid"


def isolate(root: Path | str) -> Path:
    """把总线全部指向另一个根目录（测试专用；返回该根）。

    为什么要有这个函数而不是让每个测试自己挨个赋 EVENTS/STATUS/CMD/CONF/PID：
    漏一个就污染真实留档，而这类遗漏**不会让测试变红**——它只是往仓库里
    多写几行。集中一个入口，隔离才不会靠记性。
    """
    global RUNS, EVENTS, STATUS, CMD, CONF, PID, PROJECTS
    RUNS = Path(root)
    EVENTS = _p_events()
    STATUS = _p_status()
    CMD = _p_cmd()
    CONF = _p_conf()
    PID = _p_pid()
    PROJECTS = RUNS.parent / "projects"
    RUNS.mkdir(parents=True, exist_ok=True)
    return RUNS


def cancel_dir() -> Path:
    """取消标志目录。**必须每次从 RUNS 现算**：写成模块常量会让
    "只重定向 E.RUNS"的测试仍然往真实仓库 runs/_cancel 写文件——
    测试污染真实留档是我自己这轮踩过两次的同型坑（见 test_report 的时序错）。"""
    return RUNS / CANCEL_SUBDIR


def decide_path() -> Path:
    return RUNS / DECIDE_NAME

LEASE_NAME = "resume_lease.json"
# 租约有效期：一次干净 attempt 实测约 7~31 分钟，取 45 分钟。
# 必须显著大于单轮耗时，否则守护还在正常续跑就会被别人当"崩了"抢租约；
# 又必须有限，否则"unlink 之前崩"会让工单永久卡死（实测缺口之一）。
LEASE_TTL_S = 45 * 60
# ack 等待：命令通道是单槽，UI 必须等上一条被受理才能发下一条
ACK_WAIT_S = 6.0


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def atomic_write(p: Path, text: str) -> None:
    """原子替换写（tmp + os.replace）。

    ⚠️ 这里**不做**"写后回读比对"（我曾加上，随即回退，理由记下来防再犯）：
      daemon_cmd.json 是**双写者**文件（UI 写命令、守护写回执），两次写的
      replace 之间本来就可能夹进对方的新内容——回读比对会把这种**合法的
      并发覆盖**误判成"落盘不一致"而抛错，把一个能正常工作的系统改得偶发
      崩溃。落盘确认只该用在**单写者**的关键文件上（decision.json 在
      approval.write_decision 里做了），并且要写清是谁在写。
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def atomic_write_text(p: Path, text: str) -> None:
    """统一原子写入口（阶段 2 把所有 write_text 迁到这里）。
    失败照原样抛——调用方要么留痕要么降级，不允许静默。"""
    atomic_write(p, text)


def write_json(p: Path, obj, *, what: str = "", strict: bool = True) -> bool:
    """原子写 JSON。失败必发 `write_failed` 事件（不留静默），
    strict=True 时继续上抛（让调用栈知道），strict=False 时返回 False。"""
    try:
        atomic_write(p, json.dumps(obj, ensure_ascii=False, indent=1, default=str))
        return True
    except OSError as e:
        emit("write_failed", path=str(p), what=what or p.name,
             error=f"{type(e).__name__}: {e}"[:200])
        if strict:
            raise
        return False


def load_json(p: Path) -> tuple[Optional[dict], str]:
    """(数据, 错误)。err 非空 ⇒ data 为 None。

    不提供"坏了就当没有"的单向返回：调用方必须写一行处置 err 的代码
    （emit 留痕 / 标盲区 / 拒绝该轮），这正是本契约要消灭的静默吞异常。
    """
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as e:
        return None, f"corrupt: {e.msg} at line {e.lineno} col {e.colno}"
    except OSError as e:
        return None, f"io: {type(e).__name__}: {e}"[:160]


def emit(kind: str, **fields) -> dict:
    """落一条事件。所有可观测动作（含被忽略的命令）都必须发事件——
    静默丢弃 = 交互层的'没读到当没有'。"""
    ev = {"ts": now(), "kind": kind, **fields}
    RUNS.mkdir(parents=True, exist_ok=True)
    with _p_events().open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    return ev


def tail_events(n: int = 40) -> list[dict]:
    try:
        lines = _p_events().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append({"ts": "?", "kind": "events-line-broken", "raw": ln[:120]})
    return out


def write_status(patch: dict) -> dict:
    """浅合并进状态快照并原子覆盖，附心跳时刻。"""
    st = read_status() or {}
    st.update(patch)
    st["updated_at"] = now()
    write_json(_p_status(), st, what="daemon_status", strict=False)
    return st


def read_status() -> dict | None:
    st, err = load_json(_p_status())
    return st if not err else None


# ───────────────────────── 运行时配置 ─────────────────────────

def load_conf() -> dict:
    """读运行时配置覆盖层。坏文件**必须留痕**后返回空 dict：
    返回空是降级（回落到 llm_config），静默返回空是隐瞒——守护会带着
    "用户以为关掉了的采样"继续跑，这正是实测白烧一轮的成因。
    是否 fail-closed 由调用方（daemon.load_runtime_conf）决定。"""
    data, err = load_json(_p_conf())
    if err and err != "missing":
        emit("conf_unreadable", path=str(_p_conf()), reason=err,
             fallback="llm_config_only")
    return data or {}


def save_conf(conf: dict) -> bool:
    """存运行时配置。返回是否落盘成功——enable/disable 若静默失败，
    重启后开关状态会与用户看到的不一致，调用方必须能据此报错。"""
    return write_json(_p_conf(), conf, what="daemon_conf", strict=False)


# ───────────────────────── 命令通道（seq + ack） ─────────────────────────

def send_cmd(cmd: str, **args) -> int:
    """发起控制命令：seq 自增 + 原子替换。返回 seq。

    ⚠️ seq 读不出来时**不再当成 0**（旧写法 `except: pass`）：那样新命令会与
      守护已消费的旧命令同号，被 `seq > last_seen` 判据直接跳过——命令"发出去了
      但永远不生效"，且双方都不知道。现在退化为"以文件 mtime 秒数为基准"并留痕，
      保证一定比历史 seq 大。
    """
    cur = _p_cmd()
    data, err = load_json(cur)
    base = 0
    if data is not None:
        try:
            base = int(data.get("seq", 0))
        except (TypeError, ValueError):
            err = err or f"bad_seq_field: {data.get('seq')!r}"
    # 文件不存在是**首次发命令**的正常态，不是异常：给它留痕会让事件流
    # 每轮刷一条假告警，真正读不出来的时候反而被噪音盖掉。
    if err and err != "missing":
        emit("cmd_seq_unreadable", reason=err, cmd=cmd)
        # 退化成单调递增基准：宁可 seq 很大，也不能与守护已消费的旧命令同号
        # （同号会被 `seq > last_seen` 判据跳过 ⇒ 命令发出去却永远不生效）
        try:
            base = max(base, int(cur.stat().st_mtime))
        except OSError:
            base = max(base, int(time.time()))
    seq = base + 1
    atomic_write(cur, json.dumps({"seq": seq, "cmd": cmd, "args": args,
                                  "ts": now(), "acked_seq": (data or {}).get(
                                      "acked_seq", 0)},
                                 ensure_ascii=False))
    return seq


def cmd_state() -> dict:
    """命令通道快照：{seq, cmd, ts, acked_seq, pending}。读不出来返回空 dict。"""
    data, err = load_json(_p_cmd())
    if err and data is None:
        return {}
    data = data or {}
    seq = int(data.get("seq", 0) or 0)
    acked = int(data.get("acked_seq", 0) or 0)
    return {"seq": seq, "cmd": data.get("cmd"), "ts": data.get("ts"),
            "acked_seq": acked, "pending": seq > 0 and acked < seq}


def pending_cmd() -> dict | None:
    """未被受理的命令（单槽通道的占位者）。有则不该再发新命令。"""
    st = cmd_state()
    if st.get("pending"):
        return {"seq": st["seq"], "cmd": st["cmd"], "ts": st["ts"]}
    return None


def ack_cmd(seq: int, **result) -> bool:
    """守护侧：把 acked_seq 推到 seq 并附结果。重复 ack 不报错（幂等）。"""
    data, err = load_json(_p_cmd())
    if err and data is None:
        emit("cmd_ack_failed", seq=seq, reason=err)
        return False
    data = data or {}
    data["acked_seq"] = max(int(data.get("acked_seq", 0) or 0), int(seq))
    data["ack_ts"] = now()
    data["result"] = {k: v for k, v in result.items()}
    ok = write_json(_p_cmd(), data, what="daemon_cmd_ack", strict=False)
    if not ok:
        emit("cmd_ack_failed", seq=seq, reason="write_failed")
    return ok


def wait_for_ack(seq: int, timeout_s: float = ACK_WAIT_S) -> tuple[bool, dict]:
    """发起方等 ack。返回 (是否受理, 命令通道末态)。

    为什么要等：单槽通道下"发了"≠"守护看到了"。不等就会发生两条命令相互覆盖
    ——后写的赢，前一条静默消失，UI 显示成功但守护根本没执行（阶段 1 要堵的洞）。
    """
    t0 = time.time()
    st: dict = {}
    while time.time() - t0 < timeout_s:
        st = cmd_state()
        if int(st.get("acked_seq", 0) or 0) >= seq:
            return True, st
        time.sleep(0.2)
    return False, st


def send_cmd_and_wait(cmd: str, timeout_s: float = ACK_WAIT_S, **args):
    """UI/CLI 的唯一推荐发命令方式：有未受理命令先拒绝，发完必等 ack。
    返回 (ok, info)，info 含 reason（busy / no_ack / acked）。"""
    p = pending_cmd()
    if p:
        emit("command_not_sent", reason="previous_unacked",
             blocked_by=p, new_cmd=cmd)
        return False, {"reason": "busy", "blocked_by": p}
    seq = send_cmd(cmd, **args)
    ok, st = wait_for_ack(seq, timeout_s)
    if not ok:
        emit("command_unacked", seq=seq, cmd=cmd, timeout_s=timeout_s)
    return ok, {"seq": seq, "reason": "acked" if ok else "no_ack", "state": st}


def read_cmd() -> dict | None:
    data, err = load_json(_p_cmd())
    if err and err != "missing":
        emit("cmd_file_unreadable", reason=err)
    return data


# ───────────────────────── 决定意向（单一写入者） ─────────────────────────

def stage_decide(ticket: str, decision: str, *, approver: str, note: str,
                 token: str) -> int:
    """UI 把决定意向写进 daemon_decide.json，**由守护落 decision.json**。

    为什么绕一道：decision.json 是修复放行的唯一凭据，若 UI 与守护都能写它，
    "谁批的/批的什么"就可能被后写者覆盖或绕过校验（审批链就不可信了）。
    意向文件只当传输层，落盘与记账都走 ApprovalStore 一条路。

    ⚠️ 覆盖风险如实处理：本通道只留最后一条意向。若上一条还没提交成功、
      这次又是**另一张工单**，前一条会被顶掉——这里留痕，让 UI 侧能在
      阶段 5 用状态机（AWAITING_DECISION 一次只放一张）从设计上避免并发。
    """
    p = decide_path()
    cur, _ = load_json(p)
    cur = cur or {}
    seq = int(cur.get("seq", 0) or 0) + 1
    prev_uncommitted = int(cur.get("seq", 0) or 0) > int(
        cur.get("committed_seq", 0) or 0)
    if prev_uncommitted and cur.get("ticket") != ticket:
        emit("decide_overwritten", dropped=cur, new_ticket=ticket,
             note="上一条意向尚未提交即被覆盖")
    atomic_write(p, json.dumps({"seq": seq, "ticket": ticket,
                                "decision": decision, "approver": approver,
                                "note": note, "token": token,
                                "committed_seq": int(
                                    cur.get("committed_seq", 0) or 0),
                                "ts": now()}, ensure_ascii=False))
    return seq


def peek_decide() -> tuple[dict | None, str]:
    """读意向，**不删**。守护用它决定要不要提交。"""
    data, err = load_json(decide_path())
    if err and err != "missing":
        emit("decide_unreadable", reason=err)
        return None, err
    if not data:
        return None, err or "missing"
    if int(data.get("seq", 0)) <= int(data.get("committed_seq", 0) or 0):
        return None, "already_committed"     # 幂等：重复轮询不重复落盘
    return data, ""


def commit_decide(seq: int) -> bool:
    """decision.json 已成功落盘后调用：把 committed_seq 推到 seq。

    为什么不"读后即删"：删掉意向后如果落盘失败（磁盘满/权限/崩溃），
    用户按下的批准就**永久消失**，而工单还停在待批——比不批更糟，
    因为人会以为自己批过了。现在意向活到提交成功为止，失败就在下一轮重试。
    """
    p = decide_path()
    data, err = load_json(p)
    if err or not data:
        return False
    if int(data.get("seq", 0) or 0) != int(seq):
        emit("decide_commit_mismatch", want=seq,
             have=int(data.get("seq", 0) or 0))
        return False
    data["committed_seq"] = int(seq)
    ok = write_json(p, data, what="decide_commit", strict=False)
    if ok:
        emit("decide_committed", seq=int(seq), ticket=data.get("ticket"),
             decision=data.get("decision"))
    return ok


def clear_decide() -> None:
    """意向已提交且无更新排队时清理文件（不留垃圾）。"""
    data, err = load_json(decide_path())
    if err or not data:
        return
    if int(data.get("seq", 0) or 0) <= int(data.get("committed_seq", 0) or 0):
        try:
            decide_path().unlink()
        except OSError as e:
            emit("decide_clear_failed", error=str(e)[:120])


def take_decide() -> dict | None:
    """兼容旧接口：读并清空（阶段 1 测试与开发通道用）。
    守护正式链路请用 peek_decide/commit_decide 两段式——**不要用本函数**，
    它会在读到后就删除意向，落盘失败时批准丢失。"""
    data, err = peek_decide()
    if err or not data:
        return None
    try:
        decide_path().unlink()
    except OSError as e:
        emit("decide_clear_failed", error=str(e)[:120])
    return data


# ───────────────────────── 取消通道（取代硬杀） ─────────────────────────

def cancel_flag(pid: int) -> Path:
    return cancel_dir() / str(pid)


def request_cancel(pid: int, *, reason: str = "user") -> bool:
    """请子进程在下一个窗口边界自行收尾。

    为什么不用 os.kill(pid, 15)：Windows 上它是 TerminateProcess——目标进程
    **不执行 finally**，于是 Play 模式不退、采样回调残留、进度文件停在半途，
    编辑器被一个"看起来已经结束"的进程占住（实测撞出，也是 rc=15 的来源）。
    代价要如实说：取消粒度从"立即"变成"最晚一个窗口边界（约 2.4 分钟）"，
    UI 必须显示"取消已受理，等待窗口边界"。
    """
    cancel_dir().mkdir(parents=True, exist_ok=True)
    ok = write_json(cancel_flag(pid), {"pid": int(pid), "reason": reason,
                                       "ts": now()}, what="cancel_flag",
                    strict=False)
    if ok:
        emit("cancel_requested", target_pid=int(pid), reason=reason)
    return ok


def cancel_requested(pid: int) -> bool:
    return cancel_flag(pid).exists()


def clear_cancel(pid: int) -> None:
    try:
        cancel_flag(pid).unlink()
    except OSError:
        pass          # 清不掉不影响正确性（下一轮会重新判定），此处允许静默


def sweep_cancel_flags() -> int:
    """启动期清扫：删掉指向**已死进程**的陈旧取消标志。返回清扫数。

    ⚠️ 判据只用"进程是否还活着"，不能用"标志是否过期"：文件通道是我们唯一
      能让采样子进程自己收尾的手段（不能用 os.kill 硬杀，否则它的 finally 不
      执行、编辑器停在 Play 态）。把活着的子进程的标志按"太旧"删掉，等于
      亲手剥夺停止它的能力——孤儿会一直占着编辑器，而且再没人知道。
    反过来，活进程持有取消标志意味着上一代留下的是**没停下来过的孤儿**，
    必须留痕告警（不清标志，但要说出来）。
    """
    n = 0
    alive_holders = []
    if not cancel_dir().exists():
        return 0
    for f in sorted(cancel_dir().iterdir()):
        try:
            pid = int(f.stem)
        except ValueError:
            pid = -1
        if pid > 0 and pid_alive(pid):
            # 读一眼是谁的请求，便于报告"上一代留下的孤儿在等取消"
            try:
                req = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                req = {}
            alive_holders.append({"pid": pid, "reason": req.get("reason"),
                                  "since": req.get("ts")})
            continue
        try:
            f.unlink()
            n += 1
        except OSError:
            emit("cancel_flag_sweep_failed", path=str(f))
    if n:
        emit("cancel_flags_swept", n=n)
    if alive_holders:
        emit("orphan_child_holding_cancel", holders=alive_holders,
             note="上一代守护留下的采样子进程仍在运行，标志已保留")
    return n


# ───────────────────────── 工单租约（防永久卡单） ─────────────────────────

def acquire_lease(ticket_dir: Path, *, holder: Optional[int] = None,
                  ttl_s: int = LEASE_TTL_S) -> bool:
    """抢到返回 True。判据：持有者进程还活着 **且** 未过期，才算被别人占着。

    只满足"文件存在"不构成占用——续跑流程会在跑完时才 unlink，中途崩溃就留下
    永不自愈的标记（实测缺口）。加 TTL 后崩在 unlink 之前，最多 ttl_s 后被重新接手。
    """
    lp = ticket_dir / LEASE_NAME
    held, err = _lease_state(lp, ttl_s)
    if held:
        return False
    if err and err != "missing":
        emit("lease_unreadable", ticket=ticket_dir.name, reason=err)
    return write_json(lp, {"pid": int(holder if holder is not None
                                      else os.getpid()),
                           "ts": now(), "ttl_s": int(ttl_s)},
                      what="resume_lease", strict=False)


def release_lease(ticket_dir: Path) -> None:
    try:
        (ticket_dir / LEASE_NAME).unlink()
    except OSError:
        pass


def _lease_state(lp: Path, ttl_s: int) -> tuple[bool, str]:
    data, err = load_json(lp)
    if err:
        return False, err
    data = data or {}
    try:
        born = time.mktime(time.strptime(str(data.get("ts")), "%Y-%m-%dT%H:%M:%S"))
        ttl = int(data.get("ttl_s", ttl_s) or ttl_s)
    except (ValueError, TypeError):
        return False, "corrupt: bad lease ts/ttl"
    expired = (time.time() - born) > ttl
    pid = int(data.get("pid", -1) or -1)
    alive = pid_alive(pid)
    if alive and not expired:
        return True, f"held by pid {pid}"
    return False, ("expired" if expired else "holder_dead")


def lease_state(ticket_dir: Path, ttl_s: int = LEASE_TTL_S) -> dict:
    """给 UI 展示用：{held, reason, holder}。"""
    data, err = load_json(ticket_dir / LEASE_NAME)
    held, why = _lease_state(ticket_dir / LEASE_NAME, ttl_s)
    return {"held": held, "reason": why if held else (err or why),
            "holder": (data or {}).get("pid") if held else None}


def pid_alive(pid: int) -> bool:
    """跨平台探活。

    ⚠️ Windows 上 os.kill(pid, 0) **不是探活**：除 CTRL_C/CTRL_BREAK 外的
    任何 sig 都会经 TerminateProcess 无条件杀掉目标（exit code=sig）——
    用它"查活着没"等于把守护杀了。Windows 走 OpenProcess +
    GetExitCodeProcess(STILL_ACTIVE)；POSIX 才是 signal 0 语义。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        # 句柄必须按 c_void_p 收：默认 restype=c_int 会把 64 位句柄截断，
        # 后续 GetExitCodeProcess 拿着坏句柄失败 ⇒ 活守护被误判成死的。
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_ulong))
        k32.GetExitCodeProcess.restype = ctypes.c_int
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        h = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5    # 5=ACCESS_DENIED ⇒ 进程存在
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259      # 259=STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def daemon_running() -> tuple[bool, dict | None]:
    """(在不在, 状态快照)。双判据：_p_pid() 存在 + 心跳新鲜（<15s）。
    只看 _p_pid() 会把僵尸/复用 _p_pid() 当活着；只看心跳会把挂掉没删 pid 当死。"""
    st = read_status()
    pid_ok = False
    try:
        pid = int(_p_pid().read_text(encoding="utf-8").strip())
        pid_ok = pid_alive(pid)
    except (OSError, ValueError):
        pid_ok = False
    hb_ok = False
    if st and st.get("updated_at"):
        try:
            t = time.mktime(time.strptime(st["updated_at"], "%Y-%m-%dT%H:%M:%S"))
            hb_ok = (time.time() - t) < 15
        except ValueError:
            hb_ok = False
    return (pid_ok and hb_ok), st
