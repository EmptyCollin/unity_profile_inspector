"""I23：输出配置候选（只读）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.config_plan import propose  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--max-shadow-distance", type=int, default=40)
    ap.add_argument("--max-anti-aliasing", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    items = [asdict(x) for x in propose(
        a.project, max_shadow_distance=a.max_shadow_distance,
        max_anti_aliasing=a.max_anti_aliasing)]
    text = json.dumps({"proposals": items}, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
