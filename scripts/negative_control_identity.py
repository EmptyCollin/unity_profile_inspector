"""I26-ext 负对照：把每个 case 的原始代码当 patch（identity）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="test_project_20261115_123247")
    a = ap.parse_args()
    run = ROOT / "runs" / a.run_id
    plans = json.loads((run / "plan_repeat.json").read_text(encoding="utf-8"))
    cf = {}
    for line in (run / "counterfactual.jsonl").read_text(
            encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            cf[r.get("unit_id")] = r
        except Exception:  # noqa: BLE001
            pass
    from upi.core.patch_shape import classify
    from upi.core.repair_candidate import (metric_allowed,
                                           semantic_risk_for_diffs)
    from upi.core.repair_verify import measurement_status

    rows = []
    for uid, doc in plans.items():
        variants = doc.get("variants") or []
        if not variants:
            rows.append((uid, "-", "no_variant", "", "", "", ""))
            continue
        v = variants[0]
        diffs = v.get("diffs") or []
        ident = [{"find": d.get("find", ""), "replace": d.get("find", "")}
                 for d in diffs]
        risk = semantic_risk_for_diffs(ident)
        shape = classify(ident)
        r = cf.get(uid, {})
        window = str(r.get("window") or "")
        after_n = int(r.get("after_n") or 1)
        mstatus = measurement_status(window=window, after_n=after_n,
                                     before_n=3)
        metric = str(r.get("metric_status") or v.get("metric") or "")
        family = str(r.get("patch_family") or "")
        mok = metric_allowed(family, metric) if family and metric else None
        verdict = "semantic_risk" if risk else mstatus
        if shape == "identity":
            verdict = "identity_noop"
        elif verdict == "measured":
            verdict = "no_effect/delta=0"
        rows.append((uid, shape, risk or "-", window or "-",
                     str(after_n), str(mok), verdict))

    print("| unit | identity shape | semantic | window | after_n | metric_ok | gate |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()
    print("说明：identity patch = 原代码 replace 回自己；"
          "没有真实 delta，按 delta=0 处理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
