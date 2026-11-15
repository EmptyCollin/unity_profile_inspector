"""I25：审批码。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.approval import approval_code, verify  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


c = approval_code("secret", "delete", "bl-123")
ok("4 位", len(c) == 4 and c.isalnum(), c)
ok("确定性", c == approval_code("secret", "delete", "bl-123"))
ok("正确码通过", verify("secret", "delete", "bl-123", c))
ok("错误码拒绝", not verify("secret", "delete", "bl-123", "0000"))
ok("不同 target 拒绝", not verify("secret", "delete", "bl-999", c))
ok("不同 secret 拒绝", not verify("other", "delete", "bl-123", c))

print(f"\n==== I25 审批码测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
