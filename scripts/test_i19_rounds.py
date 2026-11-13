"""I19 失败分组/轮次测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.failure_group import RoundState, bisect_groups  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


st = RoundState(round_no=1)
ok("有失败 → 下一轮", st.next_round(["u1", "u2"], 2))
ok("round 递增", st.round_no == 2)
ok("失败进 history", st.history["u1"] == ["round1:failed"])
ok("到上限停", not st.next_round(["u1"], 2))
ok("无失败停", not RoundState(1).next_round([], 3))
ok("二分 4 个", bisect_groups(["a", "b", "c", "d"]) == [["a", "b"], ["c", "d"]])
ok("单个不再分", bisect_groups(["a"]) == [["a"]])

print(f"\n==== I19 失败分组测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
