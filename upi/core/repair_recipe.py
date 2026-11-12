"""I8d 第一版源码级 repair recipe。

当前只做一个可证伪的竖切：`RefreshItemLabelsBody` 每帧 `"x"+inventory` 分配。
做法不是按文件行硬切片，而是：
- 从当前源码定位方法/循环；
- 插入 lastItemCounts 字段；
- 仅当数量变化时更新 Text；
- 语义保持（文本内容不变，只减少重复赋值/分配）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from upi.core.models import EditPlan
from upi.core.source_index import SourceIndex

PLAYBOOK = "PB-OP-ITEM-LABEL-CACHE"
FIELD_ANCHOR = "        private bool busy, dragging;"
FIELD_ADD = "        private int[] lastItemCounts;"


def plan_item_label_cache(project_root: Path | str,
                          source_index: Optional[SourceIndex] = None) -> Optional[EditPlan]:
    root = Path(project_root).resolve()
    idx = source_index or SourceIndex.build(root)
    span = idx.resolve("ChromaGame.RefreshItemLabelsBody")
    if span is None:
        return None
    file = root / span.file
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    start = span.start_line - 1
    end = span.end_line
    body = "\n".join(lines[start:end])
    # 只接受当前这个精确循环；源码变了就 refuse，不按旧行号硬套。
    loop = re.search(
        r"(?m)^\s*for \(int i = 0; i < itemCountLabels\.Length; i\+\+\)\n"
        r"\s*if \(itemCountLabels\[i\] != null\) itemCountLabels\[i\]\.text = \"x\" \+ inventory\[i\];",
        body,
    )
    if loop is None:
        return None
    old_loop = loop.group(0)
    new_loop = (
        "            if (lastItemCounts == null || lastItemCounts.Length != itemCountLabels.Length)\n"
        "                lastItemCounts = new int[itemCountLabels.Length];\n"
        "            for (int i = 0; i < itemCountLabels.Length; i++)\n"
        "                if (itemCountLabels[i] != null && lastItemCounts[i] != inventory[i])\n"
        "                {\n"
        "                    lastItemCounts[i] = inventory[i];\n"
        "                    itemCountLabels[i].text = \"x\" + inventory[i];\n"
        "                }"
    )
    if FIELD_ANCHOR not in text:
        return None
    diffs = [
        {"file": span.file, "find": FIELD_ANCHOR, "replace": FIELD_ANCHOR + "\n" + FIELD_ADD,
         "expect_count": 1, "_why": "缓存上次显示的库存数量"},
        {"file": span.file, "find": old_loop, "replace": new_loop,
         "expect_count": 1, "_why": "数量未变时不重复拼字符串/赋值"},
    ]
    return EditPlan(
        playbook_id=PLAYBOOK,
        diffs=diffs,
        invariants_assert=["库存文本内容与原逻辑一致", "库存变化时仍会更新"],
        side_effect="none",
        touched_assets=[span.file],
        permission_tier="auto",
    )
