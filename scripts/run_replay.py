"""执行 sim/replay/cXX.json 的项目命令。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.replay_exec import ReplayExecutor  # noqa: E402
from upi.core.replay_spec import load_replay  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--project", default="sim/project")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    spec = load_replay(a.replay)
    ex = ReplayExecutor(a.project)
    steps = ex.run(spec)
    report = {"ok": True, "replay_id": spec.replay_id, "steps": steps}
    out = Path(a.out) if a.out else (ROOT / "runs" / f"replay_{spec.replay_id}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "out": str(out)}, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
