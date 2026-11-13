"""I9c：证据模式 replay 复验（observed，不冒充 verified）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import replay_observe as RO  # noqa: E402
from upi.agents import nodes as N            # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


CAND = {"sources": [{"file": "Assets/A.cs", "symbol": "Type.M"}],
        "score": 10.0, "mechanism": "hot_path"}
ANCH = ["Assets/A.cs::Type.M"]

hit = RO.observe([CAND], ANCH)
ok("按 file::symbol 命中目标", hit is not None and hit["score"] == 10.0, hit)
ok("非目标不命中", RO.observe([{**CAND, "sources": [{"file": "Assets/B.cs",
                                                   "symbol": "Type.M"}]}], ANCH) is None)

ok("前后都空 → unknown", RO.judge(None, None)[0] == "unknown")
ok("改后消失 → absent", RO.judge(hit, None)[0] == "absent")
ok("分数明显下降 → reduced",
   RO.judge(hit, {**hit, "score": 4.0})[0] == "reduced")
ok("分数持平 → present",
   RO.judge(hit, {**hit, "score": 9.8})[0] == "present")

# ── verify 节点证据分支（capture 打桩，不碰 Unity）──────────────
TMP = Path(tempfile.mkdtemp(prefix="upi_replay_verify_"))
nd = N.make_nodes(TMP / "run", live_llm=False, knowledge_db=TMP / "k.db")


def st_with(before_score=10.0):
    return {
        "project": str(ROOT / "test_project"),
        "replay": str(TMP / "replay.json"),
        "issue": {"source": "evidence",
                  "scene": "Assets/Scenes/00_Bootstrap.unity"},
        "hypotheses": [{"code_anchors": ANCH}],
        "records": [],
        "attempts": 0,
        "pre_attempt": {"stamp": "x", "evidence": True, "before_path": "",
                        "floor_before": None, "floor_before_err": None,
                        "before_obs": {"file": "Assets/A.cs", "method": "M",
                                       "score": before_score,
                                       "mechanism": "hot_path"}},
    }


real = RO.capture
try:
    RO.capture = lambda *a, **k: [{**CAND, "score": 4.0}]
    r = nd["verify"](st_with())
    v = r.get("verdict") or {}
    ok("有 replay → 产出 verdict", bool(v), str(r)[:160])
    ok("状态 insufficient（不是 verified）", v.get("status") == "insufficient")
    ok("verification_status=unverified", v.get("verification_status") == "unverified")
    ok("target_present=True（目标仍在）", v.get("target_present") is True)
    ok("理由写明 observed=reduced", "observed=reduced" in str(v.get("reason")),
       v.get("reason"))

    RO.capture = lambda *a, **k: []
    r2 = nd["verify"](st_with())
    v2 = r2.get("verdict") or {}
    ok("目标消失 → target_present=False", v2.get("target_present") is False)
    ok("理由写明 observed=absent", "observed=absent" in str(v2.get("reason")),
       v2.get("reason"))
finally:
    RO.capture = real

# 没有 replay：明确交人，不伪造结论
st_noreplay = st_with()
st_noreplay.pop("replay")
r3 = nd["verify"](st_noreplay)
ok("无 replay → needs_human", r3.get("route") == "needs_human", str(r3)[:120])

print(f"\n==== 证据模式 replay 复验测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
