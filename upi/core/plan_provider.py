"""真实 plan provider 的第一条通路（I3b-2c）。

只接**已有模板**：夹具 CPU 的 full-reuse 模板（`nodes._plan_for`）。
边界：
- 只在 `upi-fixture` profile 生效；随机工程没有模板 → None；
- 模板产出的 `permission_tier=blocked`（夹具脚本），因此工单 item 会被提升为
  `needs_human`，不会被批量自动执行；
- 这是“真实 EditPlan”通路，不是自动改夹具脚本的后门。
"""
from __future__ import annotations

from typing import Optional

from upi.core.cluster import IssueCluster
from upi.core.models import EditPlan

_ANCHORS = {
    "gc_alloc_per_frame": "per_frame_heap_alloc_in_hot_path",
}
_PLAYBOOKS = {"PB-REUSE-BUFFER", "PB-FULL-REUSE"}


def fixture_cpu_plan(cluster: IssueCluster, playbook: str) -> Optional[EditPlan]:
    if playbook not in _PLAYBOOKS:
        return None
    anchor = _ANCHORS.get(cluster.metric)
    if not anchor:
        return None
    try:
        from upi.config.project_profile import active_profile
        if active_profile().profile_id != "upi-fixture":
            return None
        from upi.agents.nodes import _plan_for, TemplateUnavailable
        try:
            return _plan_for(anchor)
        except TemplateUnavailable:
            return None
    except Exception:  # noqa: BLE001 - 模板不可用 = 不硬编
        return None


def default_plan_provider(cluster: IssueCluster, playbook: str) -> Optional[EditPlan]:
    return fixture_cpu_plan(cluster, playbook)
