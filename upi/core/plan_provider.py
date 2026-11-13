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


def source_plan_for_cluster(cluster: IssueCluster, *,
                            anchors: Optional[list[str]] = None,
                            project_root=None) -> Optional[EditPlan]:
    """源码驱动：锚点 → 当前源码配方。不依赖 profile_id / defect_ref。"""
    if not anchors or project_root is None:
        return None
    try:
        from upi.core.source_plan import plan_for_anchor_list
        return plan_for_anchor_list(project_root, anchors)
    except Exception:  # noqa: BLE001
        return None


def default_plan_provider(cluster: IssueCluster, playbook: str, *,
                          anchors: Optional[list[str]] = None,
                          project_root=None) -> Optional[EditPlan]:
    """产品默认：只走源码驱动（I9b）。夹具模板不再作为回退，避免答案参照
    进入产品路径；需要夹具模板的测试/彩排显式使用 `fixture_cpu_plan`。"""
    return source_plan_for_cluster(cluster, anchors=anchors,
                                   project_root=project_root)
