"""I7d 起点：从当前源码 + OpportunityFinding 生成 EditPlan。

没有 `_refs`、没有 profile_id 门、没有按行切答案；`find` 必须在当前文件里
精确命中一次，否则返回 None（交人工/拒绝），不硬写。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from upi.core.models import EditPlan, OpportunityFinding


@dataclass(frozen=True)
class Spec:
    find: str
    replace: str
    why: str
    playbook: str
    side_effect: str = "none"
    tier: str = "auto"
    invariants: tuple[str, ...] = ()


SPECS: dict[str, Spec] = {
    "ui_text_rebuild_per_frame": Spec(
        find="if (cfg.legacy_ui || Time.unscaledTime >= nextHud)",
        replace="if (Time.unscaledTime >= nextHud)",
        why="HUD 改回固定 0.1s 脏刷新，去掉每帧分支",
        playbook="PB-OP-HUD-DIRTY",
        tier="auto",
        invariants=("HUD 仍按 0.1s 刷新",),
    ),
    "per_instance_material": Spec(
        find='''                    if (tileImages[r, c] != null && cfg.legacy_material)
                    {
                        var sh = Shader.Find("UI/Default");
                        if (sh != null) tileImages[r, c].material = new Material(sh);
                    }
''',
        replace="",
        why="移除逐格 Material 实例，回到共享 UI 材质",
        playbook="PB-OP-SHARED-MATERIAL",
        side_effect="may_affect_perception",
        tier="semi",
        invariants=("色块颜色仍由 Image.color 决定",),
    ),
    "match_scan_allocation": Spec(
        find="            if (cfg.legacy_match_alloc) { var waste = new List<int>(); for (int i = 0; i < 64; i++) waste.Add(i); }\n",
        replace="",
        why="移除匹配路径的纯浪费 List 分配",
        playbook="PB-OP-NO-TEMP-ALLOC",
        tier="auto",
        invariants=("匹配结果仍走 FindMatches/HasMatch",),
    ),
    "per_clear_particle_instantiate": Spec(
        find="            StartCoroutine(FadeFx(img));\n",
        replace="",
        why="移除每次消除的 UI 粒子 Instantiate/协程（后续可换对象池）",
        playbook="PB-OP-PARTICLE-POOL",
        side_effect="may_affect_perception",
        tier="semi",
        invariants=("消除与计分逻辑不变",),
    ),
    "canvas_layout_churn": Spec(
        find="                sideRect.sizeDelta = new Vector2(1164, 624 + (Time.frameCount % 2));\n",
        replace="",
        why="移除每帧改侧栏尺寸导致的 Canvas rebuild",
        playbook="PB-OP-NO-LAYOUT-CHURN",
        tier="auto",
        invariants=("侧栏初始尺寸不变",),
    ),
    "idle_hint_scan_per_frame": Spec(
        find="            if (cfg.legacy_hint && playing && !paused) HasMatch();\n",
        replace="",
        why="移除空闲每帧全盘提示扫描",
        playbook="PB-OP-HINT-THROTTLE",
        tier="auto",
        invariants=("交换后的消除判定仍然执行",),
    ),
}


def plan_for(finding: OpportunityFinding, project_root: str | Path) -> EditPlan | None:
    spec = SPECS.get(finding.pattern_id)
    if spec is None:
        return None
    root = Path(project_root).resolve()
    rel = finding.code_anchor.file
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    text = target.read_text(encoding="utf-8", errors="replace")
    if text.count(spec.find) != 1:
        # 当前源码与模式不一致：拒绝空写/错改，交人工。
        return None
    diff = {
        "file": rel,
        "find": spec.find,
        "replace": spec.replace,
        "expect_count": 1,
        "_why": spec.why,
    }
    return EditPlan(
        playbook_id=spec.playbook,
        diffs=[diff],
        invariants_assert=list(spec.invariants),
        side_effect=spec.side_effect,  # type: ignore[arg-type]
        touched_assets=[rel],
        permission_tier=spec.tier,  # type: ignore[arg-type]
    )
