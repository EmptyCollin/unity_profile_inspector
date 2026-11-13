"""I9 守护：产品路径不得再回退夹具/答案/案例。

静态检查（读源码）+ 一个 git 检查；不依赖 Unity。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def _product_sources():
    for base in ("upi/core", "upi/agents"):
        for p in sorted((ROOT / base).rglob("*.py")):
            yield p, p.read_text(encoding="utf-8", errors="replace")


# 1) 产品代码不得引用案例/考卷路径
bad = []
for p, t in _product_sources():
    for token in ("sim/cases", "sim/replay", "test_project"):
        if token in t:
            bad.append(f"{p.relative_to(ROOT)}: {token}")
ok("产品代码无 sim/cases、sim/replay、test_project 引用", not bad, "; ".join(bad[:5]))

# 2) 默认 plan provider 不得回退夹具模板
pp = (ROOT / "upi/core/plan_provider.py").read_text(encoding="utf-8")
body = pp.split("def default_plan_provider", 1)[-1]
ok("default_plan_provider 不调用 fixture_cpu_plan",
   "fixture_cpu_plan(" not in body)
ok("夹具模板只保留显式函数", "def fixture_cpu_plan(" in pp)

# 3) 夹具正则诊断只在 legacy 分支
nodes = (ROOT / "upi/agents/nodes.py").read_text(encoding="utf-8")
ok("retrieve_candidates 只剩一个调用点",
   nodes.count("retrieve_candidates(issue, det)") == 1,
   nodes.count("retrieve_candidates(issue, det)"))
ok("诊断有显式 legacy 开关", "if _legacy_fixture_diag_enabled():" in nodes)

# 4) 体检表不在导入期绑定档案
cl = (ROOT / "upi/tooling/checklist.py").read_text(encoding="utf-8")
ok("checklist 导入期不读 active_profile",
   "FIXTURE_SPEC = active_profile()" not in cl)

# 5) 阈值/基线不随仓库提交
out = subprocess.run(["git", "ls-files", "baselines"], cwd=str(ROOT),
                     capture_output=True, text=True, encoding="utf-8",
                     errors="replace").stdout.strip()
ok("baselines/ 未被 git 跟踪", out == "", out[:120])

# 6) 静态扫描入口已降级
web = (ROOT / "upi/agents/webui.py").read_text(encoding="utf-8")
ok("静态扫描接口明确拒绝建单", "静态扫描仅作线索" in web)

print(f"\n==== 去夹具化守护测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
