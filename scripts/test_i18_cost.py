"""I18 证据能力矩阵 + 批量成本测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.batch_plan import CaseCost, estimate, plan_batches  # noqa: E402
from upi.core.evidence_capability import capability_for, plan_need  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("share 由 detect 提供", plan_need("explains_share", "regression")["producer"] == "detect")
ok("counterfactual 不是 detect", plan_need("counterfactual", "regression")["producer"] == "verify")
ok("counterfactual 明确不可用", plan_need("counterfactual", "regression")["available"] is False)
ok("prior 走知识库", plan_need("prior_hit", "regression")["producer"] == "knowledge")
ok("未知因子不可用", plan_need("bogus", "regression")["available"] is False)

cases = [CaseCost(f"c{i}", (f"F{i}.cs",), f"m{i}") for i in range(11)]
bs = plan_batches(cases, batch_size=4)
ok("11 病例分 3 批", len(bs) == 3, [len(b.cases) for b in bs])
ok("每批 <=4", all(len(b.cases) <= 4 for b in bs))

same = [CaseCost(f"c{i}", ("Same.cs",), f"m{i}") for i in range(4)]
ok("同文件不并批", len(plan_batches(same, 4)) == 4)

est = estimate(11, batch_size=4)
ok("采集次数从 11 降到 4", est.captures == 4, est)
ok("时间低于串行", est.wallclock_s < 150 + 11 * 140 + 11 * 20, est)

print(f"\n==== I18 证据/批量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
