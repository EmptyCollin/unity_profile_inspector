"""可配置的 replay 子进程驱动（I4b2b2 接线面）。

- 只执行 `suite.command`（shlex 拆分，**不经过 shell**）；
- 命令负责跑 Unity replay/Play 并落探针事件；本驱动不猜命令；
- 命令成功后读 EventLog，返回 (protocol, events)；
- 失败/超时/空命令 → ReplayDriverError，不冒充成功。
"""
from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from upi.core import events as E
from upi.core.probe_events import EventLog
from upi.core.regression import ReplaySuite
from upi.tooling import probe_control as PC
from upi.tooling.unity_cli import run_unity

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


class UnityReplayDriver:
    """夹具/工程 Unity 自动 replay（I4b2b2b2）。

    不再要求用户填 `suite.command`，而是：
      scene open → playmode start → 探针 sample:on → 等窗口 → sample:off
      → playmode stop → 只取本次新增的探针事件。

    命令名来自 `unity` CLI 运行时能力（I8），不假设 eval/screenshot。
    `cli`/`probe`/`sleep` 可注入，测试不碰真实 Editor。
    """

    def __init__(self, *, timeout_s: int = 900, event_log: EventLog | None = None,
                 warmup_s: float = 2.0, window_s: float | None = None,
                 cli: Callable[..., dict] | None = None,
                 probe: object | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.timeout_s = timeout_s
        self.event_log = event_log or EventLog()
        self.warmup_s = max(0.0, float(warmup_s))
        self.window_s = window_s
        self._cli_override = cli
        self._probe = probe or PC
        self._sleep = sleep

    def _cli(self, name: str, *extra: str, project: Path, timeout: int = 150) -> dict:
        if self._cli_override is not None:
            return self._cli_override(name, *extra, project=project)
        env = run_unity(["command", name, "--project-path", str(project), *extra],
                        timeout_s=timeout)
        return {"ok": env.success, "data": env.data,
                "errors": [e.message for e in env.errors]}

    def _events_before(self) -> int:
        try:
            events, _bad = self.event_log.read_all(compat=True)
            return len(events)
        except Exception:  # noqa: BLE001
            return 0

    def run_once(self, suite: ReplaySuite) -> tuple[str, list]:
        project = Path(suite.project) if suite.project else ROOT
        if not project.exists():
            raise ReplayDriverError(f"project 不存在：{project}")
        if not suite.scene:
            raise ReplayDriverError("suite.scene 为空：UnityReplayDriver 需要场景相对路径")
        scene = Path(suite.scene)
        if scene.is_absolute():
            raise ReplayDriverError("suite.scene 必须是工程内相对路径")
        before = self._events_before()
        started = False
        sampling = False
        try:
            wait = getattr(self._probe, "wait_alive", None)
            if callable(wait):
                st = wait(8)
                if st.get("state") != "alive":
                    raise ReplayDriverError(
                        f"探针不可用（state={st.get('state')}, age={st.get('heartbeat_age_s')}）")
            r = self._cli("scene open", "--path", scene.as_posix(), project=project)
            if not r.get("ok"):
                # 播放模式可能占着场景：先停一次再开（与 m2a_replay 同策略）
                self._cli("editor.playmode stop", project=project)
                self._sleep(2.0)
                r = self._cli("scene open", "--path", scene.as_posix(), project=project)
            if not r.get("ok"):
                raise ReplayDriverError(
                    f"scene open 失败：{r.get('errors') or r.get('data')}")

            r = self._cli("editor.playmode start", project=project)
            if not r.get("ok"):
                raise ReplayDriverError(
                    f"playmode start 失败：{r.get('errors') or r.get('data')}")
            started = True
            if self.warmup_s:
                self._sleep(self.warmup_s)

            window = self.window_s if self.window_s is not None else float(
                suite.window_s or 5)
            window = max(1.0, float(window))
            # 探针窗口按帧：60fps 近似；clamp 与 probe_control 一致（30..600）
            frames = max(30, min(600, int(round(window * 60))))
            s = self._probe.sample(True, window=frames)
            if not s.get("ok"):
                raise ReplayDriverError(
                    f"探针采样未启动（探针未装/Editor 不可达）：{s.get('detail')}")
            sampling = True
            self._sleep(window)
            self._probe.sample(False)
            sampling = False
        finally:
            if sampling:
                self._probe.sample(False)
            if started:
                self._cli("editor.playmode stop", project=project)
        events, _bad = self.event_log.read_all(compat=True)
        new_events = events[before:]
        if not new_events:
            raise ReplayDriverError(
                "replay 结束但本次没有新增探针事件（检查探针安装/采样窗口）")
        return suite.protocol_fingerprint, new_events


def driver_for(suite: ReplaySuite):
    """有 command 用命令；否则有 project+scene 用 Unity 自动 replay。"""
    if (suite.command or "").strip():
        return SubprocessReplayDriver()
    if suite.project and suite.scene:
        return UnityReplayDriver()
    return SubprocessReplayDriver()
