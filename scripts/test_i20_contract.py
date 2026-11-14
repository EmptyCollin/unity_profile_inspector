"""I20 补丁契约测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.patch_contract import PatchMeta, validate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


sites = ["Shop.Load1", "Shop.Load2"]
m = PatchMeta("u1", "sync_resources_load", ["Shop.Load1"],
              structural=True, invariant="loads==0")
r = validate(m, required_sites=sites, mechanism_class="init_only",
             find="new string('x', 8192)", replace="")
ok("未覆盖站点 → partial", r.status == "partial", r)

m2 = PatchMeta("u2", "idle_hint_scan", ["HasMatch"],
               structural=False, invariant="calls==0")
r = validate(m2, required_sites=["HasMatch"], mechanism_class="cacheable",
             find="for (i<4) HasMatch();", replace="HasMatch();")
ok("4→1 数字/结构不足 → pseudo", r.status in ("pseudo", "partial"), r)

m3 = PatchMeta("u3", "per_instance_material", ["BuildBoard"],
               structural=True, invariant="new_material==0")
r = validate(m3, required_sites=["BuildBoard"], mechanism_class="init_only",
             find="new Material(x)", replace="shared")
ok("共享材质通过", r.ok, r)

print(f"\n==== I20 补丁契约测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
