"""I26 P3：runtime ablation 开关。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.ablation import (AblationSpec, read_flags, set_flag,  # noqa: E402
                               write_flags)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
ok("guard 形式", AblationSpec("UpdateBody.Sin").guard()
   == 'if (UpiAblation.Disabled("UpdateBody.Sin"))',
   AblationSpec("UpdateBody.Sin").guard())
write_flags(TMP, {"a": True, "b": False})
ok("读 flags", read_flags(TMP) == {"a": True, "b": False}, read_flags(TMP))
set_flag(TMP, "b", True)
ok("set flag", read_flags(TMP)["b"] is True)
set_flag(TMP, "a", False)
ok("clear flag", read_flags(TMP)["a"] is False)
ok("文件存在", (TMP / "runs" / "_probe" / "ablation_flags.txt").is_file())

print(f"\n==== I26 ablation 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
