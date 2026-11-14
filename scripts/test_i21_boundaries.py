"""I21 L2：边界用例（离线）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.llm_planner import _validate_invariant  # noqa: E402
from upi.core.metric_registry import default_registry  # noqa: E402
from upi.core.project_adapter import (Command, ProjectAdapter,  # noqa: E402
                                      Replay)
from upi.core.target_metrics import metric_series  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 1) 未注册 metric → 空序列，后续必须 unmeasured
ok("未注册 metric 不伪造数据",
   metric_series({"counters": {"X": {"series": [1]}}}, "overdraw") == [])
ok("未注册 metric 不在默认表", default_registry().get("overdraw") is None)

# 2) 非法 invariant 声明
TMP = Path(tempfile.mkdtemp())
f = TMP / "Assets" / "A.cs"
f.parent.mkdir(parents=True)
f.write_text("class A { void Tick(){ var x = new Foo(); } }\n", encoding="utf-8")
diffs = [{"file": "Assets/A.cs", "find": "var x = new Foo();", "replace": ""}]
bad = {"file": "Assets/A.cs", "method": "Tick",
       "pattern": r"Bar\s*\(", "expect": 0}
v, err = _validate_invariant(TMP, diffs, bad)
ok("pattern 不在 find 中 → 拒绝", v is None, err)

good = {"file": "Assets/A.cs", "method": "Tick",
        "pattern": r"new\s+Foo", "expect": 0}
v2, err2 = _validate_invariant(TMP, diffs, good)
ok("合法声明 → 接受", v2 is not None, err2)

# 3) 任意项目命令
class FakeAdapter:
    def __init__(self):
        self.seen = []

    def describe(self):
        return {"project_id": "any", "root": TMP, "metrics": ()}

    def run_replay(self, replay):
        self.seen = [c.name for c in replay.commands]
        return [{"ok": True}]

    def capture(self, metrics):
        return {"counters": {}}

    def compile(self):
        return {"ok": True}


ad = FakeAdapter()
ok("adapter 是协议实现", isinstance(ad, ProjectAdapter) is True)
r = Replay("r", [Command("任意命令", {"a": 1})])
ad.run_replay(r)
ok("adapter 不解释命令语义", ad.seen == ["任意命令"], ad.seen)

# 4) 缺 metric 的 EditPlan 不应被当成可验证
ok("空 metric 不能通过注册表", default_registry().get("") is None)

print(f"\n==== I21 边界测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
