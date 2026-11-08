"""KnowledgeStore 口径测试（M2，DESIGN §6/§8.2/§8.7 / AGENTS §1.7、G14、G15）。

运行：C:/Python314/python.exe scripts/test_knowledge.py

覆盖：Wilson 下界、三重签名唯一性、n_effective 口径、成功率分母口径、
升维四条件拦截、体检表缺项/不达标拦截、契约自洽拒收、硬降级。

Wilson 口径备忘（本轮实测校准出来的数）：
  全成功时 LB = n/(n+z^2)，z=1.96 ⇒ z^2≈3.84
  n=10 → 0.722（拦）；n=28 → 0.879（拦）；n=35 → 0.901（放）
  即"零失败"要攒到 35 个独立场景×档位才够升维门槛——这是设计本意，
  不是实现 bug：点估计 100% 恰恰是最容易自欺的地方。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError          # noqa: E402
from upi.core.knowledge import KnowledgeStore, wilson_lower_bound  # noqa: E402
from upi.core.models import PatternPromotionProposal              # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("  " + extra if extra else ""))


def main():
    ks = KnowledgeStore(tempfile.mktemp(suffix=".db"))

    # ---- Wilson 下界 ----
    w = wilson_lower_bound(10, 10)
    chk("Wilson(10/10)<1.0 且≈0.72", 0.68 < w < 0.78, f"LB={w:.3f}")
    chk("Wilson(0/0)=0", wilson_lower_bound(0, 0) == 0.0)

    # ---- 三重签名 = 同一条经验 ----
    ks.upsert_pattern("P-A", "gc:alloc-hotpath", "cpu", "per-frame-new-list", "reuse-buffer")
    ks.upsert_pattern("P-B", "gc:alloc-hotpath", "cpu", "enum-tostring", "pregen-table")
    n = ks.db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    chk("根因签名不同 → 两条 pattern（只标签相同不算同类）", n == 2, f"n={n}")
    ks.upsert_pattern("P-A2", "gc:alloc-hotpath", "cpu", "per-frame-new-list", "reuse-buffer")
    n = ks.db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    chk("三重签名全同 → 幂等并入", n == 2, f"n={n}")

    # ---- n_effective：同一上下文重复运行只算 1 ----
    for i in range(5):
        ks.record_outcome("P-A", f"run{i}", "F-CPU-01", "high", "resolved")
    ks.record_outcome("P-A", "runX", "F-CPU-01", "low", "resolved")
    chk("n_effective=2（场景×档位去重；5+1 次运行只算 2）",
        ks.n_effective("P-A") == 2, f"n_eff={ks.n_effective('P-A')}")

    # ---- 成功率分母：needs_human 单列不入 ----
    ks.record_outcome("P-A", "runH", "F-ANIM-01", "high", "needs_human")
    ks.record_outcome("P-A", "runF", "F-GPU-01", "high", "failed")
    r = ks.rates("P-A")
    chk("分母=resolved+failed，needs_human 单列",
        r["n_success"] == 6 and r["n_failed"] == 1 and r["n_needs_human"] == 1,
        f"{r['n_success']}/{r['n_failed']}/{r['n_needs_human']}")
    chk("成功率点估计=6/7", abs(r["success_rate"] - 6 / 7) < 1e-9)

    # ---- 升维拦截：样本不足 ----
    p1 = ks.propose_promotion("P-A", "auto", {
        "tuning_holdout_gap": 0.1, "clean_fp_rate": 0.03, "ece_diff": 0.02,
        "n_eff_ratio": 0.6, "sig_transferability": 0.9, "cross_run_verdict_consistency": 0.95})
    chk("n_effective<n_min → blocked",
        p1.status == "blocked" and any("n_effective" in x for x in p1.blocked_reasons),
        str(p1.blocked_reasons))

    # ---- 全成也要攒够独立上下文：Wilson 门槛 ----
    ks2 = KnowledgeStore(tempfile.mktemp(suffix=".db"))
    ks2.upsert_pattern("P-C", "s", "cpu", "r", "a")
    full = {"tuning_holdout_gap": 0.1, "clean_fp_rate": 0.03, "ece_diff": 0.02,
            "n_eff_ratio": 0.6, "sig_transferability": 0.9,
            "cross_run_verdict_consistency": 0.95}
    for i in range(1, 11):
        ks2.record_outcome("P-C", "run", f"f{i}", "high", "resolved", realized_over_expected=1.0)
    p10 = ks2.propose_promotion("P-C", "auto", dict(full))
    chk("10/10 全成仍被 Wilson<0.9 拦（防点估计自欺）",
        p10.status == "blocked" and any("Wilson" in x for x in p10.blocked_reasons),
        f"LB={p10.success_rate_wilson_lower_bound:.3f}")

    for i in range(12, 42):
        ks2.record_outcome("P-C", "run", f"f{i}", "high", "resolved", realized_over_expected=1.0)
    p40 = ks2.propose_promotion("P-C", "auto", dict(full))
    chk("40/40 独立上下文 → proposal_ready", p40.status == "proposal_ready",
        f"LB={p40.success_rate_wilson_lower_bound:.3f} n_eff={p40.n_effective}")

    # ---- 体检表：缺项 / 不达标 一律 blocked（G14）----
    bad = dict(full); bad["tuning_holdout_gap"] = 0.5
    chk("体检表不达标 → blocked", ks2.propose_promotion("P-C", "auto", bad).status == "blocked")
    miss = dict(full); del miss["ece_diff"]
    pm = ks2.propose_promotion("P-C", "auto", miss)
    chk("体检表缺项 → blocked",
        pm.status == "blocked" and any("缺项" in x for x in pm.blocked_reasons))
    chk("体检表整体缺失 → blocked", ks2.propose_promotion("P-C", "auto", None).status == "blocked")

    # ---- 契约自洽：ready 却带拦截理由 → 构造期拒收 ----
    try:
        PatternPromotionProposal(
            pattern_id="x", n_effective=40, n_trials=40,
            success_rate_wilson_lower_bound=0.91, requested_tier="auto",
            overfit_checklist=dict(full), status="proposal_ready",
            blocked_reasons=["自相矛盾"])
        chk("ready+拦截理由 → 构造期拒收", False)
    except ValidationError:
        chk("ready+拦截理由 → 构造期拒收", True)
    try:
        PatternPromotionProposal(
            pattern_id="x", n_effective=40, n_trials=40,
            success_rate_wilson_lower_bound=0.91, requested_tier="auto",
            overfit_checklist={}, status="proposal_ready")
        chk("ready+无体检表 → 构造期拒收", False)
    except ValidationError:
        chk("ready+无体检表 → 构造期拒收", True)

    # ---- 硬降级：1 次"引入新问题被回滚"即降 semi + 复盘标记 ----
    ks2.record_outcome("P-C", "runZ", "f99", "high", "rolled_back",
                       introduced_new_issue_rolled_back=True)
    row = ks2.db.execute(
        "SELECT tier, needs_review, n_new_issue FROM patterns WHERE id='P-C'").fetchone()
    chk("硬降级：tier=semi + 复盘标记 + 计数", row == ("semi", 1, 1), str(row))
    pa = ks2.propose_promotion("P-C", "auto", dict(full))
    chk("降级后提案被拦（n_new_issue>0）",
        pa.status == "blocked" and any("引入新问题" in x for x in pa.blocked_reasons))

    # ---- detector FP 统计按 id×version 分桶 ----
    ks2.bump_detector_stat("DET-GPU-BATCHBREAK", "v1", alert=True)
    ks2.bump_detector_stat("DET-GPU-BATCHBREAK", "v1", alert=True, false_positive=True)
    ks2.bump_detector_stat("DET-GPU-BATCHBREAK", "v2", alert=True)
    f1 = ks2.fp_rate("DET-GPU-BATCHBREAK", "v1")
    f2 = ks2.fp_rate("DET-GPU-BATCHBREAK", "v2")
    chk("FP 按 detector_version 分桶",
        f1["fp_rate"] == 0.5 and f2["fp_rate"] == 0.0, f"v1={f1['fp_rate']} v2={f2['fp_rate']}")

    print(f"\n知识库测试 {len(PASS)}/{len(PASS) + len(FAIL)}"
          + (f"，失败项: {FAIL}" if FAIL else "，全部通过"))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
