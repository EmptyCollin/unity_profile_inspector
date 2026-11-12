"""评估者工具：对候选预测做校准/覆盖率报告。Agent 不读此输出。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.calibration import (brier, ece, interval_coverage,  # noqa: E402
                                  reliability_table)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    preds = data.get("predictions") or []
    outcomes = data.get("outcomes") or []
    report = {
        "n": len(preds),
        "ece": ece(preds, outcomes) if preds else None,
        "brier": brier(preds, outcomes) if preds else None,
        "reliability": reliability_table(preds, outcomes) if preds else [],
    }
    if data.get("intervals") and data.get("truths"):
        report["intervals"] = interval_coverage(data["intervals"], data["truths"])
    out = Path(a.out) if a.out else (ROOT / "runs" / "calibration_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
