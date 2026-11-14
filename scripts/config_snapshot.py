"""I23：配置快照 JSON。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.config_snapshot import scan  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    data = [asdict(x) for x in scan(a.project)]
    text = json.dumps({"project": str(Path(a.project).resolve()),
                       "files": data}, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
