"""I22：独立评分器。oracle 只在这里读，产品 Agent 不可读。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.findings import load_run, score_run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--paper", default=str(ROOT / "runs" / "i8_lab" / "paper.json"))
    a = ap.parse_args()
    run = load_run(a.run_id)
    if run is None:
        print(f"找不到 run: {a.run_id}", file=sys.stderr)
        return 1
    paper = json.loads(Path(a.paper).read_text(encoding="utf-8"))
    print(json.dumps(score_run(paper, run), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
