"""验证门禁前置：CaseContract / PatchClaim / 分阶段 Gate。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

MECHANISMS = {
    "match_scan_allocation",
    "ui_text_rebuild_per_frame",
    "item_label_rebuild_per_frame",
    "oversized_payload_asset",
    "per_tile_material_clone",
    "per_clear_particle_instantiate",
    "forced_canvas_rebuild_per_frame",
    "per_tile_texture_allocation",
    "idle_hint_scan_per_frame",
    "per_frame_tile_animation",
    "sync_shop_payload_load",
}

MECHANISM_METRICS = {
    "match_scan_allocation": {"gc_alloc", "memory", "frame_time"},
    "ui_text_rebuild_per_frame": {"frame_time", "gc_alloc"},
    "item_label_rebuild_per_frame": {"gc_alloc", "frame_time"},
    "oversized_payload_asset": {"memory", "gc_alloc"},
    "per_tile_material_clone": {"drawcalls", "frame_time"},
    "per_clear_particle_instantiate": {"frame_time", "gc_alloc"},
    "forced_canvas_rebuild_per_frame": {"frame_time"},
    "per_tile_texture_allocation": {"gc_alloc", "memory", "frame_time"},
    "idle_hint_scan_per_frame": {"frame_time"},
    "per_frame_tile_animation": {"frame_time"},
    "sync_shop_payload_load": {"frame_time", "memory"},
}


def patch_digest(diffs: list[dict]) -> str:
    blob = json.dumps(diffs, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
    sites: list[SiteSpec] | SiteSpec
    mechanism: str
    window_indices: list[int] = field(default_factory=list)
    expected_metric: str = ""
    semantic_risk: bool = False
    changed: bool = True
    patch_digest: str = ""
    base_digest: str = ""
    expected_saved: float = 0.0
    invariants: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    provenance: str = ""
    created_at: str = ""
    frozen_at: str = ""

    def __post_init__(self):
        if isinstance(self.sites, SiteSpec):
            self.sites = [self.sites]
        if self.sites is None:
            self.sites = []

    @property
    def site(self) -> SiteSpec:
        return self.sites[0] if self.sites else SiteSpec("")

    @property
    def frozen(self) -> bool:
        return bool(self.frozen_at)


def claim_from_diffs(diffs: list[dict], *, mechanism: str,
                     sites: list[SiteSpec], windows: list[int],
                     metric: str, semantic_risk: bool = False,
                     invariants: list[str] | None = None,
                     evidence_refs: list[str] | None = None,
                     provenance: str = "planner") -> PatchClaim:
    return PatchClaim(
        sites=sites, mechanism=mechanism, window_indices=list(windows),
        expected_metric=metric, semantic_risk=semantic_risk,
        changed=bool(diffs), patch_digest=patch_digest(diffs),
        invariants=list(invariants or []),
        evidence_refs=list(evidence_refs or []), provenance=provenance,
        frozen_at="pre_registered")


def freeze_claim(claim: PatchClaim, at: str = "frozen") -> PatchClaim:
    if not claim.frozen_at:
        claim.frozen_at = at
    return claim


def validate_claim(claim: PatchClaim, contract: CaseContract,
                   diffs: list[dict] | None = None,
                   *, require_frozen: bool = False) -> list[str]:
    errs = []
    if require_frozen and not claim.frozen_at:
        errs.append("claim_not_frozen")
    if not claim.changed:
        errs.append("identity_noop")
    if not claim.sites:
        errs.append("missing_site")
    if claim.mechanism not in MECHANISMS:
        errs.append("unknown_mechanism")
    if claim.mechanism != contract.mechanism:
        errs.append("mechanism_mismatch")
    if claim.expected_metric and claim.expected_metric not in \
            MECHANISM_METRICS.get(claim.mechanism, set()):
        errs.append("metric_not_allowed")
    if not claim.window_indices:
        errs.append("missing_window")
    if not claim.invariants:
        errs.append("missing_invariants")
    if not claim.evidence_refs:
        errs.append("missing_evidence")
    if diffs is not None and claim.patch_digest and \
            claim.patch_digest != patch_digest(diffs):
        errs.append("patch_digest_mismatch")
    for s in claim.sites:
        if not any(a.matches(s.file, s.method, s.pattern)
                   for a in contract.acceptable_sites):
            errs.append("site_not_acceptable")
            break
    return errs


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
    alloc_counts: dict[str, int] = field(default_factory=dict)
    asset_size_bytes: int | None = None
    invariant_results: dict[str, bool] = field(default_factory=dict)
    forbidden_hits: list[str] = field(default_factory=list)
    metric_sigma: float = 0.0
    k_sigma: float = 3.0


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
    if p.kind == "alloc_count_max":
        return obs.alloc_counts.get(p.pattern, 0) <= p.max_count
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
    if claim.mechanism not in MECHANISMS:
        return "unknown_mechanism", ["unknown_mechanism"]
    if claim.expected_metric and claim.expected_metric not in \
            MECHANISM_METRICS.get(claim.mechanism, set()):
        return "metric_mismatch", ["metric_not_allowed"]
    if not obs.window_indices:
        return "window_missing", ["no_window"]
    if len(obs.window_indices) < 1:
        return "window_missing", ["no_window"]
    if obs.after_n < 3:
        return "unstable", ["after_n<3"]
    if not claim.window_indices:
        return "window_missing", ["claim_no_window"]
    if not set(claim.window_indices) <= set(obs.window_indices):
        return "window_mismatch", ["claim_window_not_observed"]
    if obs.metric and claim.expected_metric \
            and obs.metric != claim.expected_metric:
        return "metric_mismatch", ["metric_mismatch"]

    if contract.predicates:
        if not any(_predicate_ok(p, obs) for p in contract.predicates):
            return "mechanism_unchanged", ["no_predicate_satisfied"]
    for s in claim.sites:
        if not any(a.matches(s.file, s.method, s.pattern)
                   for a in contract.acceptable_sites):
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
        if obs.metric_sigma > 0 and abs(obs.metric_delta) < \
                obs.k_sigma * obs.metric_sigma:
            return "no_effect", ["delta_below_k_sigma"]
    if contract.invariants and obs.invariant_results:
        missing = [x for x in contract.invariants
                   if not obs.invariant_results.get(x, False)]
        if missing:
            return "invariant_failed", missing
    if contract.forbidden and obs.forbidden_hits:
        hit = [x for x in obs.forbidden_hits if x in contract.forbidden]
        if hit:
            return "forbidden_behavior", hit
    if not obs.invariants_ok:
        return "invariant_failed", ["invariant_failed"]
    if not obs.guards_ok:
        return "guard_regressed", ["guard_regressed"]
    return "verified", []
