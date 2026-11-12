"""I7d：从机会生成 source-aware EditPlan（不写盘）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.opportunity import scan  # noqa: E402
from upi.core.opportunity_planner import plan_for  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "sim" / "project"))
    ap.add_argument("--pattern", required=True)
    ap.add_argument("--active", default="")
    ns = ap.parse_args()
    flags = None
    if ns.active:
        d = json.loads(Path(ns.active).read_text(encoding="utf-8"))
        flags = {k for k, v in d.items() if v is True}
    findings = scan(ns.project, project_id="chroma-rogue", active_flags=flags)
    hit = next((f for f in findings if f.pattern_id == ns.pattern), None)
    if hit is None:
        print(json.dumps({"ok": False, "error": "opportunity not found"}, ensure_ascii=False))
        return 2
    plan = plan_for(hit, ns.project)
    if plan is None:
        print(json.dumps({"ok": False, "error": "no source-aware plan; needs human"},
                         ensure_ascii=False))
        return 3
    print(json.dumps({"ok": True, "plan": plan.model_dump()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
