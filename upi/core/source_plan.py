"""源码驱动修复规划：从当前源码 + 代码锚点生成 EditPlan。

边界与机会 planner 一致：
- 不读 `_refs`/defect_ref/答案文件，不按 profile_id 分流；
- `find` 必须在当前文件里精确命中一次，否则返回 None（交人工），不硬写；
- 当前只覆盖已有源码配方（HUD 文本缓存 / item label 缓存 / 棋盘缓冲复用），
  锚点对不上就诚实地拒绝，不猜。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from upi.core import repair_recipe as RR
from upi.core.models import EditPlan
from upi.core.source_index import SourceIndex


def anchor_method(anchor: str) -> str:
    """把 `File.cs::Method` / `Type.Method` 归一成方法名；纯 `file:line` 返回空。"""
    a = (anchor or "").strip()
    if "::" in a:
        a = a.split("::", 1)[1]
    elif a.rsplit(":", 1)[-1].isdigit():
        return ""                       # 只有文件:行号，没有方法名
    return a.split(".")[-1].strip()


def plan_for_anchor(project_root: Path | str, anchor: str,
                    playbook: str = "",
                    source_index: Optional[SourceIndex] = None) -> Optional[EditPlan]:
    root = Path(project_root).resolve()
    method = anchor_method(anchor).lower()
    if not method:
        return None
    idx = source_index or SourceIndex.build(root)
    if "updatehud" in method:
        return RR.plan_hud_text_cache(root, idx)
    if "refreshitemlabels" in method:
        return RR.plan_item_label_cache(root, idx)
    if "findmatches" in method or "boardmodel" in method:
        return RR.plan_board_buffer_reuse(root, idx)
    return None


def plan_for_anchor_list(project_root: Path | str, anchors: list[str],
                         playbook: str = "") -> Optional[EditPlan]:
    idx = SourceIndex.build(Path(project_root).resolve())
    for a in anchors or []:
        ep = plan_for_anchor(project_root, a, playbook, source_index=idx)
        if ep is not None:
            return ep
    return None
