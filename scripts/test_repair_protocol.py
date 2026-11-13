"""I15 修复关系协议测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_protocol import ProtocolState, decide_next  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("OR 失败 → 换备选", decide_next(ProtocolState("or", 1, 3)) == "retry_alternative")
ok("OR 预算尽 → 回滚停止",
   decide_next(ProtocolState("or", 3, 3)) == "rollback_and_stop")
ok("AND 部分+独立 → 叠加",
   decide_next(ProtocolState("and", 1, 3, partial=True,
                             independent=True)) == "add_cofactor")
ok("AND 无独立证据 → 不叠加",
   decide_next(ProtocolState("and", 1, 3, partial=True,
                             independent=False)) == "retry_alternative")
ok("ONE 有站点剩余 → 继续修站点",
   decide_next(ProtocolState("one", 1, 3, sites_remaining=2)) == "fix_remaining_sites")
ok("ONE 站点清零 → 停",
   decide_next(ProtocolState("one", 1, 3, sites_remaining=0)) == "stop")
ok("verified → 停", decide_next(ProtocolState("or", 1, 3, verified=True)) == "stop")

print(f"\n==== I15 修复协议测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
