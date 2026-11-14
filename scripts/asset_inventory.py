"""I23：输出资源盘点 JSON（只读）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.asset_inventory import duplicates, scan  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--out", default="")
    ap.add_argument("--hash", action="store_true")
    a = ap.parse_args()
    items = scan(a.project, hash_assets=a.hash)
    payload = {
        "project": str(Path(a.project).resolve()),
        "asset_count": len(items),
        "assets": [asdict(x) for x in items],
        "duplicates": duplicates(items),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
