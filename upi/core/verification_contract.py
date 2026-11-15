"""验证门禁前置：CaseContract / PatchClaim / 分阶段 Gate。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SiteSpec:
    file: str
    method: str = ""
    pattern: str = ""

    def matches(self, file: str, method: str = "",
                pattern: str = "") -> bool:
        if self.file and self.file != file:
            return False
        if self.method and method and self.method != method:
            return False
        if self.pattern and pattern and self.pattern not in pattern:
            return False
        return True


@dataclass
class Predicate:
    kind: str                  # source_absent | source_present | call_count_max
    file: str = ""
    pattern: str = ""
    max_count: int = 0


@dataclass
class RequiredEffect:
    metric: str
    direction: str = "down"    # down means lower is better
    min_delta: float = 0.0


@dataclass
class CaseContract:
    case_id: str
    mechanism: str
    acceptable_sites: list[SiteSpec] = field(default_factory=list)
    predicates: list[Predicate] = field(default_factory=list)
    required_effect: RequiredEffect | None = None
    invariants: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    expected_windows: list[int] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "CaseContract":
        return CaseContract(
            case_id=str(d["case_id"]),
            mechanism=str(d.get("mechanism") or ""),
            acceptable_sites=[SiteSpec(**x)
                              for x in d.get("acceptable_sites") or []],
            predicates=[Predicate(**x) for x in d.get("predicates") or []],
            required_effect=(RequiredEffect(**d["required_effect"])
                             if d.get("required_effect") else None),
            invariants=list(d.get("invariants") or []),
            forbidden=list(d.get("forbidden") or []),
            expected_windows=[int(x) for x in d.get("expected_windows") or []],
        )


@dataclass
class PatchClaim:
    site: SiteSpec
    mechanism: str
    window_indices: list[int] = field(default_factory=list)
    expected_metric: str = ""
    semantic_risk: bool = False
    changed: bool = True


@dataclass
class Observation:
    compile_ok: bool = True
    after_n: int = 0
    window_indices: list[int] = field(default_factory=list)
    metric: str = ""
    metric_delta: float = 0.0
    invariants_ok: bool = True
    guards_ok: bool = True
    source_text: str = ""
    call_counts: dict[str, int] = field(default_factory=dict)
    asset_size_bytes: int | None = None


def load_contracts(path: Path | str) -> dict[str, CaseContract]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: CaseContract.from_dict(v) for k, v in d.items()}


def _predicate_ok(p: Predicate, obs: Observation) -> bool:
    if p.kind == "source_absent":
        return bool(p.pattern) and p.pattern not in obs.source_text
    if p.kind == "source_present":
        return bool(p.pattern) and p.pattern in obs.source_text
    if p.kind == "call_count_max":
        return obs.call_counts.get(p.pattern, 0) <= p.max_count
    if p.kind == "asset_size_max":
        return (obs.asset_size_bytes is not None
                and obs.asset_size_bytes <= p.max_count)
    return False


def evaluate(contract: CaseContract, claim: PatchClaim,
             obs: Observation) -> tuple[str, list[str]]:
    """返回 status, reasons。"""
    reasons = []
    if not claim.changed:
        return "identity_noop", ["patch_changed=false"]
    if claim.semantic_risk:
        return "semantic_risk", ["semantic_risk"]
    if not obs.compile_ok:
        return "compile_failed", ["compile_failed"]
    if not obs.window_indices:
        return "window_missing", ["no_window"]
    if len(obs.window_indices) < 1:
        return "window_missing", ["no_window"]
    if obs.after_n < 3:
        return "unstable", ["after_n<3"]
    if not claim.window_indices:
        return "window_missing", ["claim_no_window"]
    if not set(obs.window_indices) & set(claim.window_indices):
        return "window_mismatch", ["claim_window_not_observed"]
    if obs.metric and claim.expected_metric \
            and obs.metric != claim.expected_metric:
        return "metric_mismatch", ["metric_mismatch"]

    if contract.predicates:
        if not any(_predicate_ok(p, obs) for p in contract.predicates):
            return "mechanism_unchanged", ["no_predicate_satisfied"]
    if not any(s.matches(claim.site.file, claim.site.method, claim.site.pattern)
               for s in contract.acceptable_sites):
        return "wrong_site", ["site_not_acceptable"]
    if claim.mechanism != contract.mechanism:
        return "wrong_mechanism", ["mechanism_mismatch"]
    if contract.required_effect:
        e = contract.required_effect
        if e.metric and obs.metric != e.metric:
            return "metric_mismatch", ["required_metric"]
        if e.direction == "down" and obs.metric_delta < e.min_delta:
            return "no_effect", ["delta_below_min"]
        if e.direction == "up" and -obs.metric_delta < e.min_delta:
            return "no_effect", ["delta_below_min"]
    if not obs.invariants_ok:
        return "invariant_failed", ["invariant_failed"]
    if not obs.guards_ok:
        return "guard_regressed", ["guard_regressed"]
    return "verified", []
