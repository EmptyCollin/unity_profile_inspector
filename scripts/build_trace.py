"""I26：构建一次 capture 的 trace/window 指标。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.trace_analysis import build_trace, write_trace  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-id", required=True)
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.write:
        p = write_trace(a.cap_id, a.runs)
        print(json.dumps({"ok": True, "path": str(p)}, ensure_ascii=False))
        return 0
    print(json.dumps(build_trace(a.cap_id, a.runs),
                     ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
