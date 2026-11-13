"""I15 站点完整性审计测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.site_audit import (audit_tokens, removed_tokens,  # noqa: E402
                                 sites_remaining)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
a = TMP / "Assets" / "A.cs"
a.parent.mkdir(parents=True)
a.write_text("void Update(){ Canvas.ForceUpdateCanvases(); }\n", encoding="utf-8")
b = TMP / "Assets" / "B.cs"
b.write_text("void Other(){ Canvas.ForceUpdateCanvases(); }\n", encoding="utf-8")

ok("删除 token 提取", any("ForceUpdateCanvases" in p for p in removed_tokens(
    "Canvas.ForceUpdateCanvases();", "")))
rem = sites_remaining(TMP, [{"find": "Canvas.ForceUpdateCanvases();",
                             "replace": ""}])
ok("多站点剩余被检出", any("ForceUpdateCanvases" in k for k in rem)
   and len(next(iter(rem.values()))) == 2, rem)
ok("token 未命中为空", audit_tokens(TMP, ["NoSuchCall"]) == {})

print(f"\n==== 站点审计测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
