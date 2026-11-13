"""I10 守护：产品路径不得再有预制配方/预制诊断/全局知识。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 1) 预制模块必须已删除
for rel in ("upi/core/repair_recipe.py", "upi/core/source_plan.py",
            "upi/core/candidate_retrieval.py", "upi/core/opportunity.py",
            "upi/core/opportunity_planner.py", "upi/core/plan_provider.py"):
    ok(f"预制模块已删除：{rel}", not (ROOT / rel).exists())

# 2) 产品代码不得引用案例/考卷路径
bad = []
for base in ("upi/core", "upi/agents"):
    for p in (ROOT / base).rglob("*.py"):
        if p.name == "factor_judge.py":
            continue  # 泄漏筛查器本身含这些词，是防御代码
        t = p.read_text(encoding="utf-8", errors="replace")
        for token in ("sim/cases", "sim/replay", "test_project"):
            if token in t:
                bad.append(f"{p.relative_to(ROOT)}: {token}")
ok("产品代码无案例/考卷引用", not bad, "; ".join(bad[:5]))

# 3) 旧诊断/模板入口不得残留
nodes = (ROOT / "upi/agents/nodes.py").read_text(encoding="utf-8")
ok("nodes 无 retrieve_candidates", "retrieve_candidates" not in nodes)
ok("nodes 无 _plan_for/模板", "_plan_for(" not in nodes and "TemplateUnavailable" not in nodes)
ok("nodes 无 legacy 夹具开关", "UPI_LEGACY_FIXTURE_DIAG" not in nodes)

# 4) 指纹不再预置
fp = (ROOT / "upi/core/fingerprint.py").read_text(encoding="utf-8")
ok("指纹不再 seed_builtin", "def seed_builtin" not in fp)

# 5) 静态扫描入口仍拒绝建单
web = (ROOT / "upi/agents/webui.py").read_text(encoding="utf-8")
ok("静态扫描接口拒绝建单", "静态扫描仅作线索" in web)

# 6) 阈值/基线不随仓库
out = subprocess.run(["git", "ls-files", "baselines"], cwd=str(ROOT),
                     capture_output=True, text=True, encoding="utf-8",
                     errors="replace").stdout.strip()
ok("baselines/ 未被 git 跟踪", out == "", out[:120])

print(f"\n==== 去预制守护测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
