"""I7c：静态机会模型 + 组合病例扫描（干净 base 必须 0 误报）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.core.models import Evidence, OpportunityCodeAnchor, OpportunityFinding  # noqa: E402
from upi.core.opportunity import scan  # noqa: E402
from _compose import compose  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


EXPECTED = {
    "ui_text_rebuild_per_frame", "per_instance_material", "match_scan_allocation",
    "sync_resources_load_on_shop", "per_clear_particle_instantiate",
    "canvas_layout_churn", "per_tile_texture", "idle_hint_scan_per_frame",
    "per_frame_tile_animation",
}


def main() -> int:
    try:
        OpportunityFinding(
            id="x", metric="cpu", reference_kind="static",
            reference_ref="", evidence=[Evidence(tool="static_scan", value=1, unit="match")],
            code_anchor=OpportunityCodeAnchor(file="A.cs"), pattern_id="p")
        ok("无 reference_ref 拒收", False)
    except Exception:
        ok("无 reference_ref 拒收", True)

    clean = scan(ROOT / "sim" / "project", project_id="clean")
    ok("干净 base 0 误报", len(clean) == 0, str(sorted({f.pattern_id for f in clean})))

    project = compose([f"c{i:02d}" for i in range(1, 12)])
    findings = scan(project, project_id="paper")
    ids = {f.pattern_id for f in findings}
    ok("组合病例扫描到 9 个机制", ids == EXPECTED, str(sorted(ids)))
    ok("机会都带 reference", all(f.reference_ref for f in findings))
    ok("机会不进入 verified 语义", all(f.status == "draft" for f in findings))
    ok("代码锚点在工程内", all(f.code_anchor.file.startswith("Assets/") for f in findings))

    ev = ROOT / "runs" / "opportunity_eval_c18.json"
    if ev.exists():
        d = json.loads(ev.read_text(encoding="utf-8"))
        ok("c18 recall（已跑评测）", d.get("recall") == 1.0, d.get("recall"))

    print(f"\n==== 机会模型/扫描测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
