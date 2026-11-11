"""常驻巡检调度（I3b-2b）。

职责边界：
- 周期性读探针事件 → 聚类 → 建批量工单；
- **不自动执行**（执行要人工批准）；
- 同一 cluster 签名在冷却期内不重复建单；
- 不负责装探针/开采样（I3b-1 已接）。

线程只做轻量文件读 + 建单；不阻塞 UI 请求。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Callable, Optional

from upi.core import events as E
from upi.core.cluster import cluster_events
from upi.core.resident import ResidentEngine
from upi.core.ticket import BatchTicket

Clock = Callable[[], float]


def _sig(clusters) -> str:
    raw = "|".join(sorted(c.cluster_id for c in clusters))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class ResidentScheduler:
    def __init__(self, engine: Optional[ResidentEngine] = None, *,
                 interval_s: float = 60.0, cooldown_s: float = 300.0,
                 clock: Clock = time.time):
        self.engine = engine or ResidentEngine()
        self.interval_s = max(5.0, float(interval_s))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self.clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_sig = ""
        self._last_ticket_ts = 0.0
        self._state = {
            "running": False, "interval_s": self.interval_s,
            "cooldown_s": self.cooldown_s, "tick_count": 0, "skipped": 0,
            "last_tick": "", "last_ticket": "", "last_clusters": 0,
            "last_error": "",
        }

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def tick(self) -> Optional[BatchTicket]:
        with self._lock:
            now = self.clock()
            self._state["last_tick"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                events, _bad = self.engine.event_log.read_all(compat=True)
                cs = cluster_events(events, merge_gap=30)
                if not cs:
                    self._state["last_clusters"] = 0
                    return None
                sig = _sig(cs)
                if (sig == self._last_sig
                        and (now - self._last_ticket_ts) < self.cooldown_s):
                    self._state["skipped"] += 1
                    return None
                t = self.engine.build_ticket(limit=8)
                self._last_sig = sig
                self._last_ticket_ts = now
                self._state.update(tick_count=self._state["tick_count"] + 1,
                                   last_ticket=t.ticket_id,
                                   last_clusters=len(cs),
                                   last_error="")
                return t
            except Exception as e:  # noqa: BLE001 - 调度线程不能死
                self._state["last_error"] = f"{type(e).__name__}: {e}"[:200]
                return None

    def _loop(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval_s)

    def start(self, interval_s: Optional[float] = None) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if interval_s is not None:
                self.interval_s = max(5.0, float(interval_s))
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="upi-resident-scheduler")
            self._state["running"] = True
            self._state["interval_s"] = self.interval_s
            self._thread.start()
            return self.status()

    def stop(self, timeout: float = 3.0) -> dict:
        with self._lock:
            self._stop.set()
            th = self._thread
            self._thread = None
        if th and th.is_alive():
            th.join(timeout=timeout)
        with self._lock:
            self._state["running"] = False
            return self.status()


_GLOBAL: Optional[ResidentScheduler] = None
_GLOCK = threading.Lock()


def get_scheduler() -> ResidentScheduler:
    global _GLOBAL
    with _GLOCK:
        if _GLOBAL is None:
            _GLOBAL = ResidentScheduler()
        return _GLOBAL


def reset_scheduler():
    global _GLOBAL
    with _GLOCK:
        if _GLOBAL is not None:
            _GLOBAL.stop()
        _GLOBAL = None
