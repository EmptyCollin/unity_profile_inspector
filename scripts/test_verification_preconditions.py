"""验证前置：CaseContract / 机制谓词 / 分阶段 gate。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.verification_contract import (ClaimSet, Observation,  # noqa: E402
                                            PatchClaim, SiteSpec,
                                            claim_from_diffs, evaluate,
                                            freeze_claim, load_contracts,
                                            patch_digest, validate_claim)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


cs = load_contracts(ROOT / "sim" / "cases" / "verification_contracts.json")
ok("11 个 contract", len(cs) == 11, len(cs))

c07 = cs["c07"]
claim = PatchClaim(SiteSpec("Assets/DevSim/Runtime/ChromaGame.cs", "Update",
                            "Canvas.ForceUpdateCanvases"),
                   "forced_canvas_rebuild_per_frame", [23],
                   "frame_time")
obs = Observation(compile_ok=True, after_n=3, window_indices=[23],
                  metric="frame_time", metric_delta=100.0,
                  source_text="private void Update() { UpdateBody(); }")
st, why = evaluate(c07, claim, obs)
ok("clean 机制通过", st == "verified", (st, why))

obs2 = Observation(compile_ok=True, after_n=3, window_indices=[23],
                   metric="frame_time", metric_delta=100.0,
                   source_text="Canvas.ForceUpdateCanvases();")
ok("机制未变拒绝", evaluate(c07, claim, obs2)[0] == "mechanism_unchanged")

ok("identity 拒绝",
   evaluate(c07, PatchClaim(claim.site, claim.mechanism, [23],
                            "frame_time", changed=False), obs)[0]
   == "identity_noop")

ok("无 window 拒绝",
   evaluate(c07, claim, Observation(compile_ok=True, after_n=3,
                                    window_indices=[], metric="frame_time",
                                    metric_delta=100.0,
                                    source_text=obs.source_text))[0]
   == "window_missing")

ok("负收益拒绝",
   evaluate(c07, claim, Observation(compile_ok=True, after_n=3,
                                    window_indices=[23],
                                    metric="frame_time", metric_delta=-1,
                                    source_text=obs.source_text))[0]
   == "no_effect")

ok("语义风险拒绝",
   evaluate(c07, PatchClaim(claim.site, claim.mechanism, [23],
                            "frame_time", semantic_risk=True), obs)[0]
   == "semantic_risk")

wrong = PatchClaim(SiteSpec("Assets/DevSim/Runtime/ChromaGame.cs", "Other", ""),
                   c07.mechanism, [23], "frame_time")
ok("错误 site 拒绝", evaluate(c07, wrong, obs)[0] == "wrong_site")

c02 = cs["c02"]
claim2 = PatchClaim(SiteSpec("Assets/DevSim/Runtime/ChromaGame.cs",
                             "UpdateHudBody", "roundText.text"),
                    c02.mechanism, [23], "frame_time")
ok("c02 缓存字段通过", evaluate(c02, claim2, Observation(
    compile_ok=True, after_n=3, window_indices=[23], metric="frame_time",
    metric_delta=50.0, source_text="private int lastHudRound;"))[0]
   == "verified")

c04 = cs["c04"]
ok("c04 asset 小通过", evaluate(c04, PatchClaim(
    SiteSpec("Assets/Resources/DevSim/payload_0.txt", "", ""),
    c04.mechanism, [28], "memory"), Observation(
        compile_ok=True, after_n=3, window_indices=[28], metric="memory",
        metric_delta=100.0, asset_size_bytes=1024))[0] == "verified")

# claim 结构 / 绑定 / 白名单
diffs = [{"file": "Assets/DevSim/Runtime/BoardModel.cs",
          "find": "var hit = new bool[N, N];",
          "replace": "var hit = _hitBuffer;"}]
c01 = cs["c01"]
claim1 = claim_from_diffs(
    diffs, mechanism=c01.mechanism,
    sites=[SiteSpec("Assets/DevSim/Runtime/BoardModel.cs",
                    "FindMatchesBody", "")],
    windows=[18, 23], metric="gc_alloc",
    invariants=["match_result_same"], evidence_refs=["e1"])
ok("claim_from_diffs digest",
   claim1.patch_digest == patch_digest(diffs), claim1.patch_digest)
ok("validate_claim 通过", validate_claim(claim1, c01, diffs) == [],
   validate_claim(claim1, c01, diffs))
ok("digest 不匹配",
   "patch_digest_mismatch" in validate_claim(
       claim1, c01, [{"find": "x", "replace": "y"}]))
bad = PatchClaim(claim1.sites, "no_such_mechanism", [23],
                 "frame_time", invariants=["x"], evidence_refs=["e"],
                 patch_digest=claim1.patch_digest)
ok("未知机制拒绝", "unknown_mechanism" in validate_claim(bad, c01))
bad2 = PatchClaim(claim1.sites, c01.mechanism, [23], "drawcalls",
                  invariants=["x"], evidence_refs=["e"])
ok("metric 白名单拒绝",
   "metric_not_allowed" in validate_claim(bad2, c01))
ok("多 site 表达", len(claim1.sites) == 1)
claim_multi = PatchClaim(
    [SiteSpec("Assets/DevSim/Runtime/BoardModel.cs", "FindMatchesBody", ""),
     SiteSpec("Assets/DevSim/Runtime/BoardModel.cs", "CountByColor", "")],
    c01.mechanism, [18, 23], "gc_alloc", invariants=["x"],
    evidence_refs=["e"])
ok("多 site 接受", evaluate(c01, claim_multi, Observation(
    compile_ok=True, after_n=3, window_indices=[18, 23], metric="gc_alloc",
    metric_delta=10, source_text="var hit = _hitBuffer;"))[0] == "verified")

# P1: 噪声 / invariant 结果 / forbidden / runtime count
ok("低于 3σ 拒绝", evaluate(c07, claim, Observation(
    compile_ok=True, after_n=3, window_indices=[23], metric="frame_time",
    metric_delta=10.0, metric_sigma=10.0,
    source_text=obs.source_text))[0] == "no_effect")
ok("invariant 结果缺失拒绝", evaluate(c07, claim, Observation(
    compile_ok=True, after_n=3, window_indices=[23], metric="frame_time",
    metric_delta=100.0, source_text=obs.source_text,
    invariant_results={"hud_layout_same": False}))[0]
   == "invariant_failed")
ok("forbidden 行为拒绝", evaluate(c07, claim, Observation(
    compile_ok=True, after_n=3, window_indices=[23], metric="frame_time",
    metric_delta=100.0, source_text=obs.source_text,
    forbidden_hits=["remove_update"]))[0] == "forbidden_behavior")
ok("runtime alloc count 谓词", evaluate(c01, claim1, Observation(
    compile_ok=True, after_n=3, window_indices=[18, 23], metric="gc_alloc",
    metric_delta=100.0, source_text="still uses new",
    alloc_counts={"per_match_bool": 0}))[0] in
   ("mechanism_unchanged", "verified"))
freeze_claim(claim1)
ok("claim 冻结", claim1.frozen is True and validate_claim(
    claim1, c01, diffs, require_frozen=True) == [], claim1.frozen_at)
unfrozen = PatchClaim(claim1.sites, c01.mechanism, [18, 23], "gc_alloc",
                      invariants=["x"], evidence_refs=["e"])
ok("未冻结拒绝", "claim_not_frozen" in validate_claim(
    unfrozen, c01, require_frozen=True))

# ClaimSet：同一 patch 多机制 / 唯一 claim id
set_claim = claim_from_diffs(
    diffs, mechanism=c01.mechanism,
    sites=[SiteSpec("Assets/DevSim/Runtime/BoardModel.cs", "FindMatchesBody", "")],
    windows=[18, 23], metric="gc_alloc",
    invariants=["match_result_same"], evidence_refs=["e1"],
    case_id="c01", unit_id="unit-06")
cs_set = ClaimSet([set_claim])
ok("ClaimSet 通过", cs_set.validate(cs) == [], cs_set.validate(cs))
ok("claim id 唯一", set_claim.claim_id.startswith("c01-"), set_claim.claim_id)
dup = ClaimSet([set_claim, set_claim])
ok("重复 claim id 拒绝", any("duplicate_claim_id" in e
                             for e in dup.validate(cs)))

print(f"\n==== 验证前置测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
