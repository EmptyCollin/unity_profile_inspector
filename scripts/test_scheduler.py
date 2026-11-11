"""I3b-2b：常驻巡检调度（离线，假引擎/假时钟）。"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E                       # noqa: E402
from upi.core import scheduler as S                    # noqa: E402
from upi.core.probe_events import ProbeEvent           # noqa: E402
from upi.core.ticket import BatchTicket                # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_sched_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def ev(rel=10):
    return ProbeEvent(kind="sample", metric="gc_alloc_per_frame", rel_frame=rel,
                      series=[1000.0] * 20, data_tier="A2")


class FakeLog:
    def __init__(self):
        self.events = [ev()]

    def read_all(self, compat=True):
        return self.events, 0


class FakeEngine:
    def __init__(self):
        self.event_log = FakeLog()
        self.n = 0

    def build_ticket(self, limit=8):
        self.n += 1
        return BatchTicket(ticket_id=f"bt-test-{self.n}", created_at=E.now())


clock = {"t": 1000.0}
eng = FakeEngine()
sch = S.ResidentScheduler(eng, interval_s=5, cooldown_s=300,
                          clock=lambda: clock["t"])

t1 = sch.tick()
ok("首轮建单", t1 is not None and t1.ticket_id == "bt-test-1")
ok("状态 tick_count=1", sch.status()["tick_count"] == 1)
t2 = sch.tick()
ok("同签名冷却内跳过", t2 is None and sch.status()["skipped"] == 1)
clock["t"] += 301
t3 = sch.tick()
ok("冷却后重新建单", t3 is not None and t3.ticket_id == "bt-test-2")

# 空事件不建单
eng.event_log.events = []
ok("无事件不建单", sch.tick() is None)
eng.event_log.events = [ev()]

# 线程启停
sch.cooldown_s = 0
sch.interval_s = 0.05
sch.start()
deadline = time.time() + 2
while time.time() < deadline and sch.status()["tick_count"] < 3:
    time.sleep(0.05)
st = sch.status()
ok("后台线程启动", st["running"] is True)
ok("后台线程 tick", st["tick_count"] >= 3)
stop = sch.stop()
ok("后台线程停止", stop["running"] is False)

# 异常不杀线程：记 last_error
class BadEngine(FakeEngine):
    def build_ticket(self, limit=8):
        raise RuntimeError("boom")


bad = S.ResidentScheduler(BadEngine(), interval_s=5, cooldown_s=0,
                          clock=lambda: 1.0)
bad.tick()
ok("异常记 last_error", "boom" in bad.status()["last_error"])

# 全局单例
S.reset_scheduler()
a = S.get_scheduler()
b = S.get_scheduler()
ok("全局 scheduler 单例", a is b)
S.reset_scheduler()

print()
if FAIL:
    print(f"==== 巡检调度测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== 巡检调度测试: {len(PASS)} 通过, 0 失败")
