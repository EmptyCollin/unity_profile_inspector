"""I15 因果结构 + 稳态判定。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.causal import (CausalFactor, CausalModel,  # noqa: E402
                             classify_relation, mechanism_class_of)
from upi.core.steady_state import check_steady, pseudo_from_patch  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


f1 = CausalFactor("a", "per_frame_new", sites=["Update", "BuildBoard"],
                  evidence="stack")
ok("同机制多站点 → one", classify_relation([f1]) == "one")
f2 = CausalFactor("b", "per_frame_load", sites=["OpenShop"], evidence="stack")
ok("独立机制+证据 → and", classify_relation([f1, f2]) == "and")
ok("无证据默认 or", classify_relation([
    CausalFactor("a", "x", sites=["A"]),
    CausalFactor("b", "y", sites=["B"])]) == "or")
cm = CausalModel("i1", "one", [f1])
ok("站点剩余", cm.sites_remaining(["Update"]) == ["BuildBoard"])
ok("机制分类 init", mechanism_class_of("每帧 new 1000 个对象") == "cacheable")
ok("机制分类 anim", mechanism_class_of("animation sample") == "necessary_per_frame")

ok("1000→0 稳态通过", check_steady([1000, 500, 0, 0]).ok)
bad = check_steady([1000, 100, 10, 10])
ok("1000→10 判伪优化", (not bad.ok) and bad.pseudo, bad.reason)
ok("持续非零判失败", not check_steady([5, 5, 5]).ok)
ok("数字补丁判伪", pseudo_from_patch("for i<32", "for i<10"))
ok("结构补丁不算伪", not pseudo_from_patch(
    "for i<32 { new X(); }", "cache.X();"))

print(f"\n==== I15 因果/稳态测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
