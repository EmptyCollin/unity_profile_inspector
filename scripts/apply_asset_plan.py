"""I23：执行单个 AssetEditPlan（默认 dry-run）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_edit import AssetEdit, apply_and_reimport, validate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--asset", required=True)
    ap.add_argument("--field", required=True)
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    before = int(a.before) if a.before.isdigit() else a.before
    after = int(a.after) if a.after.isdigit() else a.after
    edit = AssetEdit(a.asset, a.field, before, after)
    errors = validate(a.project, [edit])
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False))
        return 1
    if not a.apply:
        print(json.dumps({"ok": True, "dry_run": True,
                          "edit": asdict(edit)}, ensure_ascii=False))
        return 0
    done = apply_and_reimport(a.project, [edit])
    print(json.dumps({"ok": True, "applied": [asdict(edit)],
                      "meta_changes": len(done)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
