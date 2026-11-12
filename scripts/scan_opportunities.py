"""I7c：扫描工程静态机会，落盘给评测器。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.opportunity import save, scan  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "sim" / "project"))
    ap.add_argument("--project-id", default="")
    ap.add_argument("--scene", default="Assets/Scenes/00_Bootstrap.unity")
    ap.add_argument("--out", default=str(ROOT / "runs" / "devsim_opportunities.json"))
    ap.add_argument("--active", default="", help="运行期 feature JSON；只报激活路径")
    ns = ap.parse_args()
    active_flags = None
    if ns.active:
        d = json.loads(Path(ns.active).read_text(encoding="utf-8"))
        active_flags = {k for k, v in d.items() if v is True}
    findings = scan(ns.project, project_id=ns.project_id, scene=ns.scene,
                    active_flags=active_flags)
    p = save(findings, ns.out)
    print(json.dumps({"ok": True, "count": len(findings), "path": str(p)},
                     ensure_ascii=False))
    for f in findings:
        print(f"  {f.pattern_id:34s} {f.code_anchor.file}:{f.code_anchor.line_range}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
