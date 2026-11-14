"""I23：批量 ConfigEditPlan 执行（默认 dry-run）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.config_edit import ConfigEdit, apply, rollback, validate  # noqa: E402


def from_proposals(items: list[dict]) -> list[ConfigEdit]:
    out = []
    for x in items:
        out.append(ConfigEdit(
            path=str(x["path"]), field=str(x["field"]),
            before=x.get("before"), after=x.get("after"),
            section=str(x.get("section") or ""),
            index=int(x.get("index") or 0)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--proposals", required=True,
                    help="config_plan.py 生成的 JSON，含 proposals")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    doc = json.loads(Path(a.proposals).read_text(encoding="utf-8"))
    edits = from_proposals(doc.get("proposals") or [])
    errors = validate(a.project, edits)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False))
        return 1
    if not a.apply:
        text = json.dumps({"ok": True, "dry_run": True,
                           "edits": [asdict(e) for e in edits]},
                          ensure_ascii=False, indent=2)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    done = apply(a.project, edits)
    result = {"ok": True, "edited": len(done)}
    if a.out:
        Path(a.out).write_text(json.dumps(result, ensure_ascii=False),
                               encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
