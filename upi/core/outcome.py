"""I26-ext E0：outcome 表（状态分布之外的真实收益/成本）。"""
from __future__ import annotations

import json
from pathlib import Path


def compute(records: list[dict], *, n_findings: int = 0,
            elapsed_s: float = 0.0) -> dict:
    statuses: dict[str, int] = {}
    for r in records:
        s = str(r.get("status") or "unknown")
        statuses[s] = statuses.get(s, 0) + 1
    n_units = max(len(records), n_findings)
    verified = statuses.get("verified", 0)
    improved = statuses.get("improved_partial", 0)
    attempted = len(records)
    measured = sum(1 for r in records
                   if r.get("measurement_status") == "measured")
    semantic = sum(1 for r in records
                   if r.get("semantic_risk"))
    new_regressions = sum(1 for r in records
                          if r.get("new_clusters"))
    cost = round(elapsed_s / verified, 2) if verified else None
    return {
        "n_findings": n_findings,
        "n_units": n_units,
        "attempted": attempted,
        "measured": measured,
        "verified": verified,
        "candidate_only": statuses.get("candidate_only", 0),
        "improved_partial": improved,
        "semantic_risk": semantic,
        "unmeasured": statuses.get("unmeasured", 0),
        "unstable": statuses.get("unstable", 0),
        "unstable_baseline": statuses.get("unstable_baseline", 0),
        "metric_mismatch": statuses.get("metric_mismatch", 0),
        "no_plan": statuses.get("no_plan", 0),
        "manual_review": statuses.get("manual_review", 0),
        "new_regressions": new_regressions,
        "elapsed_s": round(elapsed_s, 1),
        "cost_per_verified_s": cost,
        "statuses": statuses,
    }


def from_run(run_dir: Path | str, *, elapsed_s: float = 0.0) -> dict:
    run = Path(run_dir)
    records = []
    cp = run / "counterfactual.jsonl"
    if cp.is_file():
        for line in cp.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    n_findings = 0
    fp = run / "findings.json"
    if fp.is_file():
        try:
            n_findings = len(json.loads(
                fp.read_text(encoding="utf-8")).get("findings") or [])
        except Exception:  # noqa: BLE001
            n_findings = 0
    return compute(records, n_findings=n_findings, elapsed_s=elapsed_s)


def markdown(doc: dict) -> str:
    lines = [
        "# outcome",
        "",
        "```text",
        f"findings          = {doc['n_findings']}",
        f"units             = {doc['n_units']}",
        f"attempted         = {doc['attempted']}",
        f"measured          = {doc['measured']}",
        f"verified          = {doc['verified']}",
        f"improved_partial  = {doc['improved_partial']}",
        f"semantic_risk     = {doc['semantic_risk']}",
        f"unmeasured        = {doc['unmeasured']}",
        f"unstable          = {doc['unstable']}",
        f"metric_mismatch   = {doc['metric_mismatch']}",
        f"no_plan           = {doc['no_plan']}",
        f"new_regressions   = {doc['new_regressions']}",
        f"elapsed_s         = {doc['elapsed_s']}",
        f"cost_per_verified = {doc['cost_per_verified_s']}",
        "```",
    ]
    return "\n".join(lines)
