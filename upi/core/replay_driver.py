"""可配置的 replay 子进程驱动（I4b2b2 接线面）。

- 只执行 `suite.command`（shlex 拆分，**不经过 shell**）；
- 命令负责跑 Unity replay/Play 并落探针事件；本驱动不猜命令；
- 命令成功后读 EventLog，返回 (protocol, events)；
- 失败/超时/空命令 → ReplayDriverError，不冒充成功。
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from upi.core import events as E
from upi.core.probe_events import EventLog
from upi.core.regression import ReplaySuite

ROOT = Path(__file__).resolve().parents[2]


class ReplayDriverError(RuntimeError):
    pass


class SubprocessReplayDriver:
    def __init__(self, *, timeout_s: int = 900, event_log: EventLog | None = None):
        self.timeout_s = timeout_s
        self.event_log = event_log or EventLog()

    def run_once(self, suite: ReplaySuite) -> tuple[str, list]:
        cmd = shlex.split(suite.command or "", posix=False)
        if not cmd:
            raise ReplayDriverError("suite.command 为空：没有可执行的 replay 命令")
        cwd = Path(suite.project) if suite.project else ROOT
        if not cwd.exists():
            raise ReplayDriverError(f"project 不存在：{cwd}")
        try:
            p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                               timeout=self.timeout_s, encoding="utf-8",
                               errors="replace")
        except FileNotFoundError as e:
            raise ReplayDriverError(f"命令不存在：{cmd[0]}") from e
        except subprocess.TimeoutExpired as e:
            raise ReplayDriverError(f"replay 超时 {self.timeout_s}s") from e
        if p.returncode != 0:
            raise ReplayDriverError(
                f"replay 退出 {p.returncode}: {(p.stderr or p.stdout or '')[-300:]}")
        events, _bad = self.event_log.read_all(compat=True)
        return suite.protocol_fingerprint, events
