"""守护进程的事件总线：进度事件流、状态快照、控制命令通道。

设计三条（都是本仓实测教训的平移）：
- 一切走文件且原子写（tmp+replace）——守护、控制台、CLI 是三个独立进程，
  内存态不共享也不可信（I9 的同型纪律）；
- 进度必须**可观测**：没检出的轮次、被拒的命令、进行到哪个夹具哪个窗口，
  都要能从事件流里读出来。"在跑"与"睡着"必须分得清（I10 的交互层版本）；
- 命令带 seq 去重：控制台崩了重发同一条不能二次生效；守护重启后旧命令
  不被重新消费（processed_seq 落状态文件）。

文件布局（全在 runs/ 下）：
  daemon_events.jsonl  进度/决定事件流（append-only）
  daemon_status.json   当前快照（原子覆盖：阶段、进度、计数、下次轮次）
  daemon_cmd.json      控制命令（最新一条，seq 去重消费）
  daemon_conf.json     运行时配置（UI 改的开关活过重启，优先于 llm_config）
  daemon.pid           守护进程 PID
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
EVENTS = RUNS / "daemon_events.jsonl"
STATUS = RUNS / "daemon_status.json"
CMD = RUNS / "daemon_cmd.json"
CONF = RUNS / "daemon_conf.json"
PID = RUNS / "daemon.pid"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def emit(kind: str, **fields) -> dict:
    """落一条事件。所有可观测动作（含被忽略的命令）都必须发事件——
    静默丢弃 = 交互层的'没读到当没有'。"""
    ev = {"ts": now(), "kind": kind, **fields}
    RUNS.mkdir(parents=True, exist_ok=True)
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    return ev


def tail_events(n: int = 40) -> list[dict]:
    try:
        lines = EVENTS.read_text(encoding="utf-8").splitlines()
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
    atomic_write(STATUS, json.dumps(st, ensure_ascii=False, indent=1,
                                    default=str))
    return st


def read_status() -> dict | None:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def send_cmd(cmd: str, **args) -> int:
    """控制台/CLI 发起控制命令：seq 自增 + 原子替换。返回 seq。"""
    cur_seq = 0
    try:
        cur_seq = int(json.loads(CMD.read_text(encoding="utf-8")).get("seq", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    seq = cur_seq + 1
    atomic_write(CMD, json.dumps({"seq": seq, "cmd": cmd, "args": args,
                                  "ts": now()}, ensure_ascii=False))
    return seq


def read_cmd() -> dict | None:
    try:
        return json.loads(CMD.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_conf() -> dict:
    try:
        return json.loads(CONF.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_conf(conf: dict) -> None:
    atomic_write(CONF, json.dumps(conf, ensure_ascii=False, indent=1,
                                  default=str))


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
    """(在不在, 状态快照)。双判据：PID 存在 + 心跳新鲜（<15s）。
    只看 PID 会把僵尸/复用 PID 当活着；只看心跳会把挂掉没删 pid 当死。"""
    st = read_status()
    pid_ok = False
    try:
        pid = int(PID.read_text(encoding="utf-8").strip())
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
