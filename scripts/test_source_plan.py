"""P1：源码驱动修复规划（去夹具化）离线验证。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import source_plan as SP            # noqa: E402
from upi.core.case_overlay import apply_case      # noqa: E402
from upi.core.cluster import cluster_events       # noqa: E402
from upi.core.plan_provider import default_plan_provider  # noqa: E402
from upi.core.probe_events import ProbeEvent      # noqa: E402
from upi.core.ticket import build_ticket          # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def cluster(metric="gc_alloc_per_frame", rel=10):
    e = ProbeEvent(kind="breach", metric=metric, rel_frame=rel, value=2000.0,
                   threshold=1500.0, ring_before=[1000.0] * 5,
                   ring_after=[2000.0] * 5, data_tier="A2")
    return cluster_events([e])[0]


TMP = Path(tempfile.mkdtemp(prefix="upi_srcplan_"))


def applied(case: str) -> Path:
    out = TMP / case
    apply_case(ROOT / "sim" / "project", ROOT / "sim" / "cases" / case, out)
    return out


# 干净 base 不应产出计划（配方要的是"当前源码里真的有这段缺陷模板"）
clean = ROOT / "sim" / "project"
ok("干净 base：HUD 锚点无计划", SP.plan_for_anchor(clean, "ChromaGame.UpdateHudBody") is None)
ok("干净 base：item 锚点无计划",
   SP.plan_for_anchor(clean, "ChromaGame.RefreshItemLabelsBody") is None)

c02 = applied("c02")
ep = SP.plan_for_anchor(c02, "ChromaGame.UpdateHudBody")
ok("c02：HUD 配方命中", ep is not None and ep.playbook_id == "PB-OP-HUD-TEXT-CACHE")
ok("c02：diff 精确命中且语义保持",
   ep is not None and len(ep.diffs) >= 5
   and ep.side_effect == "none"
   and "lastHudRound" in str(ep.diffs))

c03 = applied("c03")
ep3 = SP.plan_for_anchor(c03, "Assets/DevSim/Runtime/ChromaGame.cs::RefreshItemLabelsBody")
ok("c03：item 配方命中", ep3 is not None and ep3.playbook_id == "PB-OP-ITEM-LABEL-CACHE")

c01 = applied("c01")
ep1 = SP.plan_for_anchor(c01, "BoardModel.FindMatches")
ok("c01：board 配方命中", ep1 is not None
   and ep1.playbook_id == "PB-OP-BOARD-BUFFER-REUSE")

ok("未知锚点诚实拒绝", SP.plan_for_anchor(c02, "ChromaGame.NoSuchMethod") is None)

# default_plan_provider 不需要 fixture profile，只看锚点+工程根
c = cluster()
epd = default_plan_provider(c, "PB-OP-HUD-TEXT-CACHE",
                            anchors=["ChromaGame.UpdateHudBody"], project_root=c02)
ok("默认 provider 源码驱动（无 fixture 门）",
   epd is not None and epd.playbook_id == "PB-OP-HUD-TEXT-CACHE")

# 工单路径：带 anchor_resolver 时能挂上真实 plan
class Fp:
    def route(self, _f):
        return SimpleNamespace(hit=True, record=SimpleNamespace(
            pattern_id="pat-x", playbook_id="PB-OP-HUD-TEXT-CACHE"))


t = build_ticket([c], fp_store=Fp(), plan_provider=default_plan_provider,
                 anchor_resolver=lambda _c: ["ChromaGame.UpdateHudBody"],
                 project_root=c02)
item = t.items[0]
ok("工单带源码 plan", item.plan is not None
   and item.playbook_id == "PB-OP-HUD-TEXT-CACHE")
ok("源码配方在 auto 白名单（语义保持）", item.permission_tier == "auto"
   and item.status == "pending", f"{item.permission_tier} {item.status}")

print(f"\n==== 源码规划（去夹具化）测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
