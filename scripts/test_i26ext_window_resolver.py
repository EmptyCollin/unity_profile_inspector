"""I26-ext V1：window resolver。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.window_resolver import action_indices, index_for, resolve  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


idx = action_indices("runs/i8_lab/paper_replay.json")
ok("board -> resolve_board", index_for("board", idx) == 18, idx)
ok("hud -> resolve", index_for("hud", idx) == 23, idx)
ok("shop -> open_shop", index_for("shop", idx) == 28, idx)
ok("未知 window None", index_for("fx", idx) is None)
r = resolve("sim/cases/c01", "runs/i8_lab/paper_replay.json")
ok("c01 board 解析", r["status"] == "ok" and r["window"] == "board", r)
r2 = resolve("sim/cases/c04", "runs/i8_lab/paper_replay.json")
ok("c04 shop 解析", r2["status"] == "ok" and r2["window"] == "shop", r2)
ok("缺失 manifest -> window_missing",
   resolve("sim/cases/none", "runs/i8_lab/paper_replay.json")["status"]
   == "window_missing")

print(f"\n==== I26-ext window resolver 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
