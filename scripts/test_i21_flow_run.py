"""I21：流程中枢阶段/路由。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.flow_run import FlowRun  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
f = FlowRun(TMP, "r1")
f.enter("baseline")
ok("phase=baseline", f.state.phase == "baseline")
ok("flow.json 落盘", (TMP / "flow.json").exists())

ok("无失败 → done", f.decide(set(), round_no=1, max_rounds=2) == "done")
f2 = FlowRun(TMP / "2", "r2")
ok("有失败且未到上限 → retry",
   f2.decide({"u1"}, round_no=1, max_rounds=2) == "retry")
f3 = FlowRun(TMP / "3", "r3")
ok("到上限 → max_rounds",
   f3.decide({"u1"}, round_no=2, max_rounds=2) == "max_rounds")
data = json.loads((TMP / "flow.json").read_text(encoding="utf-8"))
ok("history 记录阶段", len(data["history"]) >= 1, data)

print(f"\n==== I21 流程中枢测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
