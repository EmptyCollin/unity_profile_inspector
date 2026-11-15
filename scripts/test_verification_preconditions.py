"""验证前置：CaseContract / 机制谓词 / 分阶段 gate。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.verification_contract import (Observation, PatchClaim,  # noqa: E402
                                            SiteSpec, evaluate,
                                            load_contracts)

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

print(f"\n==== 验证前置测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
