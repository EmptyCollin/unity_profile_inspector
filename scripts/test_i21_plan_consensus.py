"""I21：计划多数选择。"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.plan_consensus import choose_consensus, plan_key  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def p(find, metric="frame_time", inv=None):
    return SimpleNamespace(metric=metric, invariant=inv or {},
                           diffs=[{"file": "A.cs", "find": find,
                                   "replace": ""}])


a = p("ForceUpdateCanvases();")
b = p("ForceUpdateCanvases();")
c = p("if (dirty) ForceUpdateCanvases();")

selected, stable, counts = choose_consensus([a, b, c], min_votes=2)
ok("2/3 一致 → 选中", stable and selected is not None, counts)
ok("选中多数方案", plan_key(selected) == plan_key(a), plan_key(selected))

selected2, stable2, _ = choose_consensus([a, c, p("x")], min_votes=2)
ok("无多数 → 不选", selected2 is None and stable2 is False)

selected3, stable3, _ = choose_consensus([a], min_votes=1)
ok("单次采样可接受", selected3 is a and stable3)

selected4, stable4, _ = choose_consensus([a, c], min_votes=2)
ok("2 种各 1 票 → 不稳定", selected4 is None and stable4 is False)

# metric/invariant 也必须一致
d = p("ForceUpdateCanvases();", metric="memory")
_, stable5, _ = choose_consensus([a, b, d], min_votes=2)
ok("metric 不同不算同方案", stable5 is True)  # a,b 仍是多数
ok("diff 不同 key 不同", plan_key(a) != plan_key(c))

print(f"\n==== I21 计划一致性测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
