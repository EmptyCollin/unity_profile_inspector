"""I26-ext E2：测量分层。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.measure_profile import after_repeats, can_verify, get  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


ok("screen after=1", get("screen").after == 1)
ok("candidate after=1", after_repeats("candidate") == 1)
ok("final after=3", after_repeats("final") == 3)
ok("显式覆盖", after_repeats("screen", 4) == 4)
ok("只有 final 可 verified", can_verify("final", 3) is True)
ok("candidate 不可 verified", can_verify("candidate", 3) is False)
ok("screen 不可 verified", can_verify("screen", 3) is False)
ok("final after 不足不可 verified", can_verify("final", 1) is False)

print(f"\n==== I26-ext profile 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
