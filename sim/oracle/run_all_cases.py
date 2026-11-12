"""跑 22 个 devsim case：replay → 运行期特征 → 扫描 → 评测。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "sim"
SCEN = SIM / "replay" / "scenarios.json"
FEATURES = Path(tempfile.gettempdir()) / "devsim_active_features.json"
OUT = ROOT / "runs"


def run(cmd: list[str]) -> int:
    p = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8",
                       errors="replace", capture_output=True)
    if p.returncode != 0:
        print("FAIL", " ".join(cmd), "\n", (p.stdout + p.stderr)[-800:])
    return p.returncode


def main() -> int:
    scenarios = json.loads(SCEN.read_text(encoding="utf-8"))
    results = []
    for cid in sorted(scenarios):
        shot = OUT / f"devsim_case_{cid}.png"
        run([sys.executable, str(SIM / "replay" / "devsim_replay.py"),
             "--case", cid, "--wait", "1", "--out", str(shot)])
        if not FEATURES.exists():
            print("missing feature state", cid)
            continue
        found = OUT / f"devsim_opportunities_{cid}.json"
        run([sys.executable, str(ROOT / "scripts" / "scan_opportunities.py"),
             "--project", str(SIM / "project"), "--project-id", "chroma-rogue",
             "--active", str(FEATURES), "--out", str(found)])
        run([sys.executable, str(SIM / "oracle" / "evaluate.py"),
             "--case", cid, "--found", str(found)])
        ev = OUT / f"opportunity_eval_{cid}.json"
        if ev.exists():
            results.append(json.loads(ev.read_text(encoding="utf-8")))
    total = len(results)
    neg = [r for r in results if r.get("negative_control")]
    positives = [r for r in results if not r.get("negative_control")]
    summary = {
        "cases": total,
        "positive_cases": len(positives),
        "negative_cases": len(neg),
        "positive_recall_avg": round(sum(r["recall"] for r in positives) / max(1, len(positives)), 4),
        "positive_precision_avg": round(sum(r["precision"] for r in positives) / max(1, len(positives)), 4),
        "negative_ok": all(r.get("negative_ok") for r in neg),
        "results": results,
    }
    (OUT / "devsim_all_cases.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
