"""从 EditPlan 生成并冻结 PatchClaim。"""
from __future__ import annotations

import json
from pathlib import Path

from upi.core.verification_contract import (PatchClaim, SiteSpec,
                                            claim_from_diffs, freeze_claim,
                                            validate_claim)


def _sites_from_diffs(diffs: list[dict]) -> list[SiteSpec]:
    out = []
    for d in diffs:
        file = str(d.get("file") or "")
        find = str(d.get("find") or "")
        line = next((x.strip() for x in find.splitlines() if x.strip()), "")
        if file:
            out.append(SiteSpec(file=file, method="", pattern=line))
    return out


def claim_from_plan(plan, *, mechanism: str, window_indices: list[int],
                    metric: str = "", invariants: list[str] | None = None,
                    evidence_refs: list[str] | None = None,
                    provenance: str = "planner") -> PatchClaim:
    diffs = list(getattr(plan, "diffs", []) or [])
    return claim_from_diffs(
        diffs, mechanism=mechanism,
        sites=_sites_from_diffs(diffs),
        windows=list(window_indices), metric=metric or getattr(plan, "metric", ""),
        invariants=list(invariants or []),
        evidence_refs=list(evidence_refs or []),
        provenance=provenance)


def build_and_validate(plan, contract, *, window_indices: list[int],
                       evidence_refs: list[str] | None = None) -> tuple[
                           PatchClaim | None, list[str]]:
    metric = ""
    if contract.required_effect:
        metric = contract.required_effect.metric
    claim = claim_from_plan(
        plan, mechanism=contract.mechanism,
        window_indices=window_indices, metric=metric,
        invariants=contract.invariants,
        evidence_refs=evidence_refs)
    freeze_claim(claim)
    errs = validate_claim(claim, contract, getattr(plan, "diffs", None),
                          require_frozen=True)
    return (claim if not errs else None), errs


def write_claims(path: Path | str, claims: list[PatchClaim]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([
        {"sites": [s.__dict__ for s in c.sites],
         "mechanism": c.mechanism, "window_indices": c.window_indices,
         "expected_metric": c.expected_metric,
         "patch_digest": c.patch_digest, "changed": c.changed,
         "provenance": c.provenance, "frozen_at": c.frozen_at,
         "invariants": c.invariants, "evidence_refs": c.evidence_refs}
        for c in claims], ensure_ascii=False, indent=2), encoding="utf-8")
    return p
