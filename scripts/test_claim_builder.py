"""从 EditPlan 生成 claim 的测试。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.claim_builder import (build_and_validate, claim_from_plan,  # noqa: E402
                                    write_claims)
from upi.core.verification_contract import load_contracts  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


class Plan:
    metric = "frame_time"
    def __init__(self, diffs):
        self.diffs = diffs


cs = load_contracts(ROOT / "sim" / "cases" / "verification_contracts.json")
c07 = cs["c07"]
p = Plan([{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
           "find": "Canvas.ForceUpdateCanvases();",
           "replace": ""}])
claim, errs = build_and_validate(p, c07, window_indices=[23],
                                 evidence_refs=["e1"])
ok("plan -> claim", claim is not None, errs)
if claim:
    ok("digest 绑定", bool(claim.patch_digest), claim)
    ok("已冻结", claim.frozen is True, claim.frozen_at)
    ok("site 来自 diff", claim.sites[0].file.endswith("ChromaGame.cs"), claim.sites)

p2 = Plan([{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
            "find": "other", "replace": "x"}])
claim2, errs2 = build_and_validate(p2, c07, window_indices=[23],
                                   evidence_refs=["e1"])
ok("错误 site 拒绝", claim2 is None and "site_not_acceptable" in errs2, errs2)

p3 = Plan([{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
            "find": "Canvas.ForceUpdateCanvases();", "replace": ""}])
claim3, errs3 = build_and_validate(p3, c07, window_indices=[23])
ok("缺证据拒绝", claim3 is None and "missing_evidence" in errs3, errs3)

tmp = Path(tempfile.mkdtemp()) / "claims.json"
write_claims(tmp, [claim] if claim else [])
ok("claims 落盘", tmp.is_file() and "patch_digest" in tmp.read_text(encoding="utf-8"))

print(f"\n==== claim builder 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
