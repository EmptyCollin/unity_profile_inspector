"""I3b-2c：真实 plan provider（夹具 CPU 模板）+ 权限不降级。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config.project_profile import use_profile  # noqa: E402

# 夹具模板测试：显式指定内置档案（I9c 不再默认回退夹具）
use_profile("upi-fixture")

from upi.core import events as E                       # noqa: E402
from upi.core.cluster import cluster_events            # noqa: E402
from upi.core.fingerprint import FingerprintStore      # noqa: E402
from upi.core.plan_provider import (default_plan_provider,  # noqa: E402
                                    fixture_cpu_plan)
from upi.core.probe_events import ProbeEvent           # noqa: E402
from upi.core.ticket import build_ticket               # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_plan_"))
E.isolate(TMP / "runs")


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def cluster(metric="gc_alloc_per_frame"):
    ev = ProbeEvent(kind="sample", metric=metric, rel_frame=10,
                    series=[1000.0] * 20, data_tier="A2")
    return cluster_events([ev])[0]


c = cluster()
p = fixture_cpu_plan(c, "PB-REUSE-BUFFER")
ok("夹具 CPU 模板产出 plan", p is not None and p.playbook_id == "PB-FULL-REUSE")
ok("模板有真实 diffs", p is not None and len(p.diffs) >= 2)
ok("模板权限是 blocked", p is not None and p.permission_tier == "blocked")
ok("未知指标不硬编", fixture_cpu_plan(cluster("drawCalls"), "PB-REUSE-BUFFER") is None)
ok("未知 playbook 不硬编", fixture_cpu_plan(c, "PB-SHARED-MAT-MPB") is None)
ok("default provider 不再回退夹具模板",
   default_plan_provider(c, "PB-REUSE-BUFFER") is None)

fp = FingerprintStore(TMP / "fp.db")
fp.seed_builtin()
t = build_ticket([c], fp_store=fp, plan_provider=fixture_cpu_plan)
it = t.items[0]
ok("plan 挂到工单 item", it.plan is not None)
ok("blocked plan 不 pending", it.status == "needs_human" and it.permission_tier == "blocked")
ok("工单不自动执行", t.status == "awaiting_approval"
   and all(i.status != "pending" for i in t.items))
ok("playbook 以 plan 为准", it.playbook_id == "PB-FULL-REUSE")

print()
if FAIL:
    print(f"==== plan provider 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== plan provider 测试: {len(PASS)} 通过, 0 失败")
