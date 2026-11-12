"""评估者门禁：检查工程是否含测试语义；可冻结 H 集清单。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.holdout import (HoldoutManifest, audit_leakage,  # noqa: E402
                             freeze_manifest, write_manifest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--manifest", default="")
    ap.add_argument("--freeze", default="")
    ap.add_argument("--cases", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.project).resolve()

    if a.freeze:
        if not a.cases:
            print("--freeze 需要 --cases c1,c2")
            return 2
        m = freeze_manifest(a.freeze, [x.strip() for x in a.cases.split(",") if x.strip()],
                            root)
        write_manifest(m, a.manifest or (ROOT / "runs" / f"{a.freeze}.json"))
        print(f"frozen {a.freeze}: {len(m.case_ids)} cases, {len(m.source_hashes)} files")
        return 0

    hits = audit_leakage(root)
    payload = {"ok": not hits, "project": str(root), "hits": [h.model_dump() for h in hits]}
    if a.manifest:
        m = HoldoutManifest(**json.loads(Path(a.manifest).read_text(encoding="utf-8")))
        payload["holdout_id"] = m.holdout_id
        payload["cases"] = m.case_ids
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"project: {root}")
        print(f"leaks: {len(hits)}")
        for h in hits[:25]:
            print(f"  {h.file}:{h.line} [{h.term}] {h.text}")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
