"""I19 机制合并 + 批量轮次测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.batch_round import run_batch  # noqa: E402
from upi.core.mechanism_batch import (PatchRef, consolidate,  # noqa: E402
                                      group_patches)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


findings = [
    {"finding_id": "f1", "mechanism": "per_instance_material",
     "anchors": [{"file": "A.cs", "method": "BuildBoard"}]},
    {"finding_id": "f2", "mechanism": "per_instance_material",
     "anchors": [{"file": "A.cs", "method": "BuildBoard"}]},
    {"finding_id": "f3", "mechanism": "per_tile_texture",
     "anchors": [{"file": "A.cs", "method": "RefreshTiles"}]},
]
units = consolidate(findings)
ok("11 finding 同机制合并", len(units) == 2, len(units))
ok("单元覆盖多 target", len(units[0].finding_ids) == 2, units[0])
ok("不同机制不合并", units[1].mechanism == "per_tile_texture")

p1 = PatchRef("u1", ["A.cs"])
p2 = PatchRef("u2", ["B.cs"])
p3 = PatchRef("u3", ["A.cs"])
groups = group_patches([p1, p2, p3])
ok("同文件不并组", any(len(g) == 2 for g in groups) and len(groups) == 2, groups)

applied, rolled, calls = [], [], {"capture": 0}


def plan(u):
    return PatchRef(u.unit_id, ["A.cs"])


def apply(p):
    applied.append(p.unit_id)
    return True


def comp():
    return True


def capture():
    calls["capture"] += 1
    return {"ok": True}


def verify(u, obs):
    return "verified" if u.unit_id != "unit-02" else "needs_recheck"


def rollback(p):
    rolled.append(p.unit_id)
    return True


r = run_batch(units, plan=plan, apply=apply, compile_fn=comp,
              capture=capture, verify=verify, rollback=rollback)
ok("一次 capture 验全部", calls["capture"] == 1)
ok("verified 保留", r.verified == ["unit-01"], r.verified)
ok("failed 记录并回滚", r.failed.get("unit-02") == "needs_recheck"
   and r.rolled_back == ["unit-02"], (r.failed, r.rolled_back))

# 一个 unit 没方案，不应拖垮整批
units2 = consolidate([
    {"finding_id": "a", "mechanism": "m1",
     "anchors": [{"file": "A.cs", "method": "A"}]},
    {"finding_id": "b", "mechanism": "m2",
     "anchors": [{"file": "B.cs", "method": "B"}]},
])
calls2 = {"capture": 0}


def plan2(u):
    return None if u.mechanism == "m2" else PatchRef(u.unit_id, ["A.cs"])


def apply2(p):
    return True


def verify2(u, obs):
    return "verified"


def rollback2(p):
    return True


r2 = run_batch(units2, plan=plan2, apply=apply2, compile_fn=comp,
               capture=lambda: calls2.__setitem__("capture", calls2["capture"] + 1) or {},
               verify=verify2, rollback=rollback2)
ok("no_plan 不拖垮整批", r2.verified == ["unit-01"] and calls2["capture"] == 1, r2)

print(f"\n==== I19 机制合并/批量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
