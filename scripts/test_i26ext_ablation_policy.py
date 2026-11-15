"""I26-ext E4：消融判定。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.ablation_policy import decide  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("未测量禁止", decide(measured=False)[0] == "forbidden")
ok("语义风险禁止", decide(measured=True, semantic_risk="empty_replace")[0]
   == "forbidden")
ok("歧义 → required", decide(measured=True, causal_ambiguity=0.8,
                              expected_value=5, ablation_cost=1)[0]
   == "required")
ok("门槛附近 + 竞争 → optional",
   decide(measured=True, expected_gain=10, threshold=10, margin=1,
          competing_candidates=2, expected_value=5, ablation_cost=1)[0]
   == "optional")
ok("无触发禁止", decide(measured=True)[0] == "forbidden")
ok("成本大于收益禁止",
   decide(measured=True, causal_ambiguity=0.8, expected_value=1,
          ablation_cost=5)[0] == "forbidden")

print(f"\n==== I26-ext ablation policy 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
