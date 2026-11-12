"""I7c：把工具输出的 OpportunityFinding 与 case oracle 对比。

只做评测：不把 expected 回灌给扫描器。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="c18")
    ap.add_argument("--found", default=str(ROOT / "runs" / "devsim_opportunities.json"))
    ap.add_argument("--oracle-dir", default=str(ROOT / "sim" / "oracle" / "cases"))
    ap.add_argument("--out", default="")
    ns = ap.parse_args()

    oracle = json.loads((Path(ns.oracle_dir) / f"{ns.case}.json").read_text(encoding="utf-8"))
    found = json.loads(Path(ns.found).read_text(encoding="utf-8"))
    expected = oracle.get("expected_opportunities", [])
    exp_keys = {(e.get("pattern_id"), e.get("code_anchor", "").split("::")[0])
                for e in expected}
    got_keys = {(f.get("pattern_id"), f.get("code_anchor", {}).get("file"))
                for f in found}
    tp = sorted(exp_keys & got_keys)
    fp = sorted(got_keys - exp_keys)
    fn = sorted(exp_keys - got_keys)
    precision = len(tp) / max(1, len(got_keys))
    recall = len(tp) / max(1, len(exp_keys))
    negative = bool(oracle.get("negative_control"))
    out = {
        "case_id": ns.case,
        "negative_control": negative,
        "expected": len(exp_keys),
        "found": len(got_keys),
        "tp": [list(x) for x in tp],
        "fp": [list(x) for x in fp],
        "fn": [list(x) for x in fn],
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "negative_ok": (not got_keys) if negative else None,
    }
    p = Path(ns.out) if ns.out else ROOT / "runs" / f"opportunity_eval_{ns.case}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("证据：", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
