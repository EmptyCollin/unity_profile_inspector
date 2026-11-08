"""归因置信度单测（M2，DESIGN §5 / AGENTS §1.3）。
运行：C:/Python314/python.exe scripts/test_attribution.py
覆盖：公式加权、三条硬性否决、否决封顶自洽、排序与路由、非法因子拒收。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from upi.core.attribution import (FactorJudgment, build_hypothesis, rank,
                                  route, c_attr)
from upi.core.models import AttributionFactors, Evidence, Hypothesis, Issue, IssueSignature

ev = [Evidence(tool="probe", value=76947.0, unit="B/frame", data_tier="A2")]
sig = IssueSignature(dim="gc_alloc_per_frame", metric="gc_alloc_per_frame",
                     mechanism="per_frame_heap_alloc_in_hot_path", threshold=50000)
issue = Issue(signature=sig, link="cpu", evidence=ev, magnitude=68576,
              deviation_ratio=9.19)


def J(f, v, ref="runs/x/e1.json", **kw):
    return FactorJudgment(factor=f, value=v, evidence_ref=ref, **kw)


PASS: list[str] = []
FAIL: list[str] = []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ FAIL ") + name + (" " + extra if extra else ""))


strong = build_hypothesis("热路径每帧堆分配", ["hot_path_mech"], [
    J("proximity", 0.9), J("explains_share", 0.92), J("competitiveness", 0.8),
    J("prior_hit", 0.5),
    J("counterfactual", 1.0, counterfactual_tested=True,
      counterfactual_disproved=False),
    J("mechanism", 0.9)], issue)
chk("① 强假设 conf≥0.6 无否决", strong.confidence >= 0.6 and not strong.veto_reasons,
    f"({strong.confidence})")

weak = build_hypothesis("相关嫌疑", [], [
    J("proximity", 1.0), J("explains_share", 0.1), J("mechanism", 0.5)], issue)
chk("② 解释力否决封顶≤0.5 + needs_human",
    weak.confidence <= 0.5 and route([weak]) == "needs_human",
    f"({weak.confidence})")

noev = build_hypothesis("无证据假设", [], [
    J("explains_share", 0.9, ref=""), J("mechanism", 0.9, ref="")], issue)
chk("③ 全判定无证据指针 → 否决", any("证据" in v for v in noev.veto_reasons))

disproved = build_hypothesis("被实验否证", [], [
    J("explains_share", 1.0), J("proximity", 1.0), J("mechanism", 1.0),
    J("counterfactual", 0.0, counterfactual_tested=True,
      counterfactual_disproved=True)], issue)
chk("④ 反事实否证 → 否决≤0.5", disproved.confidence <= 0.5)

try:
    Hypothesis(root_cause="x", attribution_factors=AttributionFactors(),
               confidence=0.9, veto_reasons=["故意违规"])
    chk("⑤ 否决+高置信矛盾拒收", False)
except ValidationError:
    chk("⑤ 否决与高置信矛盾 → 构造期拒收", True)

chk("⑥ rank 按 conf 降序 + 空输入 needs_human",
    rank([weak, strong])[0] is strong and route([]) == "needs_human")

try:
    FactorJudgment(factor="bogus", value=0.5)
    chk("⑦ 非法因子名拒收", False)
except ValidationError:
    chk("⑦ 非法因子名构造期拒收", True)

# ⑧ 零因子裸声明：截距先验必须低于 0.5（白手起家不许进循环）
bare = c_attr(AttributionFactors())
chk("⑧ 全零因子先验 <0.5", bare < 0.5, f"({bare:.3f})")

print(f"\n归因单测 {len(PASS)}/8" + (f"，失败 {FAIL}" if FAIL else ""))
sys.exit(0 if not FAIL else 1)
