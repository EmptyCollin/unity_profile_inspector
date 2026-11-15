"""从 oracle plans + case contracts 生成并校验 claims。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.claim_builder import (build_and_validate,  # noqa: E402
                                    contract_for_target, method_for_target,
                                    write_claims)
from upi.core.verification_contract import load_contracts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", default="runs/oracle_clean_plans.json")
    ap.add_argument("--contracts", default="sim/cases/verification_contracts.json")
    ap.add_argument("--out", default="runs/claims_from_oracle.json")
    a = ap.parse_args()
    contracts = load_contracts(a.contracts)
    plans = json.loads(Path(a.plans).read_text(encoding="utf-8"))
    claims, failures = [], []
    for uid, p in plans.items():
        target = str(p.get("target") or "")
        c = contract_for_target(contracts, target)
        if c is None:
            failures.append({"unit_id": uid, "error": "no_contract"})
            continue
        plan = SimpleNamespace(diffs=p.get("diffs") or [],
                               metric=p.get("metric") or "")
        method = method_for_target(c, target)
        claim, errs = build_and_validate(
            plan, c, window_indices=c.expected_windows,
            evidence_refs=[uid], method=method, unit_id=uid)
        if claim is None:
            failures.append({"unit_id": uid, "case": c.case_id,
                             "errors": errs})
        else:
            claims.append(claim)
    out = write_claims(a.out, claims)
    print(json.dumps({"claims": len(claims), "failures": failures,
                      "path": str(out)}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
