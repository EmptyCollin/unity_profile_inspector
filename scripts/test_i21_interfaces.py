"""I21：指标注册表与项目适配器协议。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.metric_registry import MetricRegistry, default_registry  # noqa: E402
from upi.core.project_adapter import Command, ProjectAdapter, Replay  # noqa: E402
from upi.core.target_metrics import metric_series  # noqa: E402
from upi.adapters.devsim import DevSimAdapter  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


reg = default_registry()
ok("默认注册表有 frame_time", reg.get("frame_time") is not None)
ok("默认注册表有 drawcalls", reg.get("drawcalls") is not None)
ok("未注册指标返回空", reg.get("overdraw") is None)

data = {"counters": {"CPU Main Thread Frame Time": {"series": [10, 12]}}}
ok("别名解析", metric_series(data, "frame_time") == [10.0, 12.0],
   metric_series(data, "frame_time"))

tmp = Path(tempfile.mkdtemp()) / "metrics.json"
tmp.write_text('{"metrics":[{"id":"custom","aliases":["X"]}]}',
               encoding="utf-8")
custom = MetricRegistry.load(tmp)
ok("外部配置注册自定义指标", custom.get("custom") is not None)
ok("自定义别名解析", custom.series({"counters": {"X": {"series": [1]}}},
                                 "custom") == [1.0])

cmd = Command(name="任意项目命令", args={"x": 1})
ok("Command 不限制命令名", cmd.name == "任意项目命令")
ok("Replay 组合命令", Replay("r", [cmd]).commands[0].args["x"] == 1)
ok("ProjectAdapter 是协议", isinstance(object(), ProjectAdapter) is False)

dev = DevSimAdapter(tmp.parent)
ok("DevSimAdapter 实现协议", isinstance(dev, ProjectAdapter) is True)
ok("DevSimAdapter 只在 adapter 层", dev.describe().adapter == "devsim")
ok("任意 wait 命令可映射",
   dev.run_replay(Replay("r", [Command("wait", {"seconds": 0})]))[0]["type"] == "wait")

for rel in ("upi/core/project_adapter.py", "upi/core/metric_registry.py"):
    text = (ROOT / rel).read_text(encoding="utf-8")
    ok(f"{rel} 无项目名", "ChromaGame" not in text and "BoardModel" not in text)

print(f"\n==== I21 接口测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
