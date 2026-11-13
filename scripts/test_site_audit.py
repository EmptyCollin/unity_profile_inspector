"""I15 站点完整性审计测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.site_audit import (audit_patterns, audit_tokens,  # noqa: E402
                                 removed_tokens, sites_remaining)

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

ok("删除模式提取", any("ForceUpdateCanvases" in p for p in removed_tokens(
    "Canvas.ForceUpdateCanvases();", "")))
all_hits = audit_patterns(TMP, [r"\bForceUpdateCanvases\s*\("])
ok("全工程模式命中 2 文件", len(all_hits[next(iter(all_hits))]) == 2, all_hits)
rem = sites_remaining(TMP, [{"file": "Assets/A.cs",
                             "find": "Canvas.ForceUpdateCanvases();",
                             "replace": ""}])
ok("只审计补丁所在文件",
   rem == {} or all(any("A.cs" in x for x in v) for v in rem.values()), rem)
a.write_text("void Update(){ }\n", encoding="utf-8")
rem2 = sites_remaining(TMP, [{"file": "Assets/A.cs",
                              "find": "Canvas.ForceUpdateCanvases();",
                              "replace": ""}])
ok("补丁后不误报其它文件", rem2 == {}, rem2)
ok("token 未命中为空", audit_tokens(TMP, ["NoSuchCall"]) == {})

print(f"\n==== 站点审计测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
